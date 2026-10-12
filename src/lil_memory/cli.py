"""Command-line interface: `lil-memory <command>`.

Heavy modules are imported inside commands, so `serve` starts fast and stdout stays clean
for the MCP protocol.
"""

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from urllib.parse import quote

from lil_memory import __version__

DEFAULT_VAULT = "~/lil-memory"
DEFAULT_PORT = 8765
GITIGNORE = "http-secret\n"
LABELS = {True: "ok  ", False: "FAIL", None: "note"}
SECRET_WARNING = (
    "Anyone with this URL can read and change your memories. "
    "`lil-memory serve --http --rotate` replaces it."
)


def vault_path(args: argparse.Namespace) -> Path:
    raw = getattr(args, "path", None) or args.vault or os.environ.get("LIL_MEMORY_VAULT")
    return Path(raw or DEFAULT_VAULT).expanduser().resolve()


def read_config(root: Path) -> dict | None:
    try:
        with open(root / ".lil-memory" / "config.toml", "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return None


def fail(message: str) -> int:
    print(f"lil-memory: {message}", file=sys.stderr)
    return 1


def no_vault(root: Path) -> int:
    return fail(f"no vault at {root}; run `lil-memory init {root}` first")


def cmd_init(args: argparse.Namespace) -> int:
    from lil_memory import vault
    from lil_memory.index import Index, check_fts5

    check_fts5()
    root = vault_path(args)
    data = root / vault.DATA_DIR
    (root / "global").mkdir(parents=True, exist_ok=True)
    data.mkdir(exist_ok=True)
    files = {"config.toml": f'format = "{vault.FORMAT}"\n', ".gitignore": GITIGNORE}
    for name, text in files.items():
        if not (data / name).exists():
            (data / name).write_text(text, encoding="utf-8")
    index = Index(root)
    index.refresh(force=True)
    count = index.stats()["memories"]
    index.close()
    print(f"Vault ready at {root} ({count} memories).")
    flag = "" if root == Path(DEFAULT_VAULT).expanduser().resolve() else f" --vault {root}"
    print(f"Next: lil-memory install claude-desktop{flag}")
    return 0


def http_secret(root: Path, rotate: bool) -> str:
    """The secret in the HTTP URL: 128 random bits, readable only by the user, never in git."""
    data = root / ".lil-memory"
    path = data / "http-secret"
    if rotate or not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(16) + "\n")
        os.chmod(path, 0o600)
        ignore = data / ".gitignore"
        lines = ignore.read_text().splitlines() if ignore.exists() else []
        if "http-secret" not in lines:
            ignore.write_text("\n".join([*lines, "http-secret"]) + "\n")
    return path.read_text().strip()


def http_url(root: Path, port: int, tunnel: str | None = None, rotate: bool = False) -> str:
    """The secret MCP address: local, or behind a tunnel host for clients such as ChatGPT."""
    secret = http_secret(root, rotate)
    if not tunnel:
        return f"http://127.0.0.1:{port}/mcp/{secret}"
    host = tunnel.strip().removeprefix("https://").removeprefix("http://").rstrip("/")
    if not re.fullmatch(r"[A-Za-z0-9.-]+(:[0-9]+)?", host):
        raise ValueError(f"--tunnel takes a host such as abc.trycloudflare.com, not {tunnel!r}")
    return f"https://{host}/mcp/{secret}"


def cmd_url(args: argparse.Namespace) -> int:
    root = args.root
    try:
        print(http_url(root, args.port, args.tunnel))
    except ValueError as e:
        return fail(str(e))
    if not args.tunnel:
        print("For ChatGPT, add --tunnel <the https host your tunnel prints>.", file=sys.stderr)
    print(SECRET_WARNING, file=sys.stderr)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from lil_memory.index import check_fts5

    check_fts5()
    root = args.root
    if args.rotate and not args.http:
        return fail("--rotate only applies to --http")
    if not args.http:
        from lil_memory.server import serve

        serve(root)
        return 0
    from lil_memory.server import serve_http

    url = http_url(root, args.port, rotate=args.rotate)
    print(
        f"lil memory is serving {root} over HTTP at\n\n    {url}\n\n"
        f"For ChatGPT, put a tunnel in front of it, e.g. `cloudflared tunnel --url "
        f"http://127.0.0.1:{args.port}`,\nthen run `lil-memory url --tunnel <host>` for the "
        f"address to add as a connector with no authentication.\n{SECRET_WARNING}",
        file=sys.stderr,
    )
    serve_http(root, url.rsplit("/", 1)[1], args.port)
    return 0


def cmd_reindex(args: argparse.Namespace) -> int:
    from lil_memory.index import Index

    root = args.root
    start = time.perf_counter()
    index = Index(root)
    index.reindex()
    count = index.stats()["memories"]
    index.close()
    print(f"Indexed {count} memories in {time.perf_counter() - start:.2f} s.")
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    from lil_memory import install

    root = args.root
    try:
        done = [install.install(args.client, root)]
        if args.client == "codex":  # Codex does not show MCP server instructions to the model
            from lil_memory.server import INSTRUCTIONS

            agents = install.codex_config().with_name("AGENTS.md")
            done.append(install.install_instructions(agents, INSTRUCTIONS))
    except (OSError, ValueError) as e:
        return fail(str(e))
    for path, backup in done:
        print(f"Added lil-memory to {path}" + (f" (backup: {backup.name})" if backup else ""))
    print(f"Restart {args.client} to load it.")
    return 0


def obsidian_config() -> Path:
    """Obsidian's own list of known vaults. We only read it."""
    if sys.platform == "darwin":
        base = Path.home() / "Library/Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ["APPDATA"])
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "obsidian" / "obsidian.json"


def launch(target: str) -> None:
    """Hand a URL or folder to the operating system, like double-clicking it."""
    if sys.platform == "win32":
        os.startfile(target)
    else:
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.run([opener, target], check=True, capture_output=True)


def cmd_open(args: argparse.Namespace) -> int:
    root = args.root
    try:
        vaults = json.loads(obsidian_config().read_text(encoding="utf-8"))["vaults"]
        ids = [k for k, v in vaults.items() if Path(v["path"]).expanduser().resolve() == root]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        vaults = None
    try:
        if vaults is None:
            launch(str(root))
            print(f"Opened {root}. Get Obsidian (obsidian.md) to browse it as linked notes.")
        elif ids:
            launch("obsidian://open?vault=" + quote(ids[0], safe=""))
            print(f"Opened {root} in Obsidian.")
        else:
            launch("obsidian://open")
            print(
                f'Obsidian does not know this vault yet. Choose "Open folder as vault" and select\n'
                f"\n    {root}\n\nOnce that is done, `lil-memory open` goes straight there."
            )
    except (OSError, subprocess.CalledProcessError) as e:
        return fail(f"could not open {root}: {e}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from lil_memory import install, vault
    from lil_memory.index import Index, check_fts5

    results: list[bool | None] = []

    def report(ok: bool | None, message: str) -> None:
        results.append(ok)
        print(f"{LABELS[ok]}  {message}")

    report(sys.version_info >= (3, 11), f"Python {sys.version.split()[0]}")
    try:
        check_fts5()
        report(True, "SQLite has FTS5")
    except RuntimeError as e:
        report(False, str(e))
    root = vault_path(args)
    config = read_config(root)
    if config is None:
        report(False, f"no vault at {root}; run `lil-memory init {root}`")
    else:
        fmt = config.get("format")
        report(True if fmt == vault.FORMAT else None, f"vault at {root} (format {fmt})")
        try:
            index = Index(root)
            index.refresh(force=True)
            stats = index.stats()
            index.close()
            report(True, f"index: {stats['memories']} memories, {stats['active']} active")
            report(True, f"index at {index.path}")
            if (root / vault.DATA_DIR / "index.sqlite").exists():
                report(None, "old index in the vault is unused; delete .lil-memory/index.sqlite*")
            if stats["malformed"]:
                report(None, f"{stats['malformed']} memories have malformed frontmatter")
        except Exception as e:  # doctor reports problems; it never crashes on them
            report(False, f"index: {e}")
    for client in install.CLIENTS:
        entry = install.installed(client)
        if entry is None:
            report(None, f"{client}: not installed (lil-memory install {client})")
        elif str(root) in entry.get("args", []):
            report(True, f"{client}: installed")
        else:
            report(None, f"{client}: installed for a different vault")
    return 1 if False in results else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lil-memory",
        description="Your AI memory as a folder of Markdown files.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--vault", help=f"vault folder (default: $LIL_MEMORY_VAULT or {DEFAULT_VAULT})"
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    def command(name: str, func, help: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, parents=[common], help=help, description=help)
        p.set_defaults(func=func)
        return p

    command("init", cmd_init, "create a vault").add_argument("path", nargs="?")
    serve = command("serve", cmd_serve, "run the MCP server (stdio, or HTTP for remote clients)")
    serve.add_argument("--http", action="store_true", help="serve at a secret URL on 127.0.0.1")
    serve.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help="HTTP port (default: %(default)s)"
    )
    serve.add_argument("--rotate", action="store_true", help="replace the secret URL")
    command("install", cmd_install, "add lil-memory to a client's MCP config").add_argument(
        "client", choices=["claude-desktop", "claude-code", "codex"]
    )
    url = command("url", cmd_url, "print the secret HTTP address to connect ChatGPT and others")
    url.add_argument("--tunnel", help="your tunnel's https host, e.g. abc.trycloudflare.com")
    url.add_argument("--port", type=int, default=DEFAULT_PORT, help="HTTP port")
    command("open", cmd_open, "open the vault in Obsidian to explore your memories")
    command("reindex", cmd_reindex, "rebuild the index from the files")
    command("doctor", cmd_doctor, "check FTS5, the vault and client installs")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    args.root = vault_path(args)
    if args.command not in ("init", "doctor") and read_config(args.root) is None:
        return no_vault(args.root)  # every other command needs an existing vault
    try:
        return args.func(args)
    except RuntimeError as e:
        return fail(str(e))
