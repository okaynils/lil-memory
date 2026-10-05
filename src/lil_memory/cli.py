"""Command-line interface: `lil-memory <command>`.

Heavy modules are imported inside commands, so `serve` starts fast and stdout stays clean
for the MCP protocol.
"""

import argparse
import os
import secrets
import sys
import time
import tomllib
from pathlib import Path

from lil_memory import __version__

DEFAULT_VAULT = "~/lil-memory"
DEFAULT_PORT = 8765
GITIGNORE = "index.sqlite*\nhttp-secret\n"
LABELS = {True: "ok  ", False: "FAIL", None: "note"}


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


def cmd_serve(args: argparse.Namespace) -> int:
    from lil_memory.index import check_fts5

    check_fts5()
    root = vault_path(args)
    if read_config(root) is None:
        return no_vault(root)
    if args.rotate and not args.http:
        return fail("--rotate only applies to --http")
    if not args.http:
        from lil_memory.server import serve

        serve(root)
        return 0
    from lil_memory.server import serve_http

    secret = http_secret(root, args.rotate)
    local = f"http://127.0.0.1:{args.port}"
    print(
        f"lil memory is serving {root} over HTTP at\n\n    {local}/mcp/{secret}\n\n"
        f"For ChatGPT, expose it with a tunnel, e.g. `cloudflared tunnel --url {local}`,\n"
        f"then add https://<tunnel-host>/mcp/{secret} as a connector with no authentication.\n"
        "Anyone with this URL can read and change your memories. "
        "`serve --http --rotate` replaces it.",
        file=sys.stderr,
    )
    serve_http(root, secret, args.port)
    return 0


def cmd_reindex(args: argparse.Namespace) -> int:
    from lil_memory.index import Index

    root = vault_path(args)
    if read_config(root) is None:
        return no_vault(root)
    start = time.perf_counter()
    index = Index(root)
    index.reindex()
    count = index.stats()["memories"]
    index.close()
    print(f"Indexed {count} memories in {time.perf_counter() - start:.2f} s.")
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    from lil_memory import install

    root = vault_path(args)
    if read_config(root) is None:
        return no_vault(root)
    try:
        config, backup = install.install(args.client, root)
    except (OSError, ValueError) as e:
        return fail(str(e))
    print(f"Added lil-memory to {config}" + (f" (backup: {backup.name})" if backup else ""))
    print(f"Restart {args.client} to load it.")
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
        "client", choices=["claude-desktop", "claude-code"]
    )
    command("reindex", cmd_reindex, "rebuild the index from the files")
    command("doctor", cmd_doctor, "check FTS5, the vault and client installs")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except RuntimeError as e:
        return fail(str(e))
