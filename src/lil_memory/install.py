"""Register the lil memory MCP server in a client's JSON or TOML config, keeping the rest."""

import json
import os
import re
import shutil
import sys
import tempfile
import tomllib
from datetime import UTC, datetime
from pathlib import Path

SERVER_NAME = "lil-memory"


def claude_desktop_config() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    if sys.platform == "win32":
        return Path(os.environ["APPDATA"]) / "Claude/claude_desktop_config.json"
    return Path.home() / ".config/Claude/claude_desktop_config.json"  # unofficial Linux builds


def claude_code_config() -> Path:
    """User-scope MCP servers live in .claude.json's top-level "mcpServers"."""
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home()) / ".claude.json"


def codex_config() -> Path:
    """Shared by the Codex CLI, IDE extension and ChatGPT desktop app: [mcp_servers.<name>]."""
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "config.toml"


CLIENTS = {
    "claude-desktop": claude_desktop_config,
    "claude-code": claude_code_config,
    "codex": codex_config,
}


def server_command(vault: Path) -> tuple[str, list[str]]:
    """How the client should start us: this Python, unless it lives in uvx's throwaway cache."""
    args = ["serve", "--vault", str(vault)]
    if "archive-v0" in Path(sys.executable).parts:  # run via `uvx lil-memory`
        return "uvx", [SERVER_NAME, *args]
    return sys.executable, ["-m", "lil_memory", *args]


def entry(client: str, vault: Path) -> dict:
    command, args = server_command(vault)
    if client == "claude-code":
        return {"type": "stdio", "command": command, "args": args, "env": {}}
    return {"command": command, "args": args}


def servers_key(path: Path) -> str:
    return "mcp_servers" if path.suffix == ".toml" else "mcpServers"


def load(path: Path) -> tuple[str, dict]:
    """The config's text and parsed contents. Raises ValueError for anything unexpected."""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    try:
        if path.suffix == ".toml":
            data = tomllib.loads(text)
        else:
            data = json.loads(text) if text.strip() else {}
    except ValueError as e:  # JSONDecodeError and TOMLDecodeError are both ValueErrors
        raise ValueError(f"{path} could not be parsed ({e}); not touching it") from e
    if not isinstance(data, dict) or not isinstance(data.get(servers_key(path), {}), dict):
        raise ValueError(f"{path} does not look like an MCP client config; not touching it")
    return text, data


def toml_with_entry(text: str, entry: dict) -> str:
    """Drop our [mcp_servers.lil-memory] table (and its subtables) and append a fresh one.
    Editing as text keeps the user's comments and layout; the caller checks the result."""
    name = rf"""(?:{SERVER_NAME}|"{SERVER_NAME}"|'{SERVER_NAME}')"""
    ours = re.compile(rf"\[\s*mcp_servers\s*\.\s*{name}\s*[].]")
    kept, skipping = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("["):
            skipping = bool(ours.match(line.lstrip()))
        if not skipping:
            kept.append(line)
    body = "\n".join(kept).rstrip()
    table = [f"[mcp_servers.{SERVER_NAME}]"]
    table += [f"{key} = {json.dumps(value)}" for key, value in entry.items()]
    return (body + "\n\n" if body else "") + "\n".join(table) + "\n"


def render(path: Path, text: str, data: dict, entry: dict) -> str:
    if path.suffix != ".toml":
        data.setdefault("mcpServers", {})[SERVER_NAME] = entry
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    new = toml_with_entry(text, entry)
    try:
        ok = tomllib.loads(new)["mcp_servers"][SERVER_NAME] == entry
    except (tomllib.TOMLDecodeError, KeyError):
        ok = False
    if not ok:
        raise ValueError(f"could not update {path} safely; add [mcp_servers.{SERVER_NAME}] by hand")
    return new


def write(path: Path, text: str) -> Path | None:
    """Atomically replace a file, keeping a timestamped backup of the old one. Returns it."""
    backup = None
    if path.exists():
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup = path.with_name(f"{path.name}.{stamp}.bak")
        shutil.copy2(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        if backup:
            shutil.copymode(backup, tmp)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise
    return backup


def install(client: str, vault: Path, config: Path | None = None) -> tuple[Path, Path | None]:
    """Add or replace our server entry, keeping every other key. Returns (config, backup)."""
    config = config or CLIENTS[client]()
    text, data = load(config)  # raises before anything is written
    return config, write(config, render(config, text, data, entry(client, vault)))


START, END = "<!-- lil-memory:start -->", "<!-- lil-memory:end -->"


def install_instructions(path: Path, instructions: str) -> tuple[Path, Path | None]:
    """Put our usage instructions in an always-loaded instruction file such as Codex's global
    AGENTS.md, for clients that do not pass MCP server instructions on to the model.
    Only the marked block is ours; the rest of the file is left as it is."""
    block = "\n".join(
        [
            START,
            "## lil memory",
            "",
            "These come from the lil-memory MCP server (`lil-memory install codex` rewrites this "
            "block). Its tools are recall, remember, update, forget, get and list_scopes; "
            "search for them if they are not loaded yet.",
            "",
            instructions.strip(),
            END,
        ]
    )
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if text.count(START) == text.count(END) == 1 and text.index(START) < text.index(END):
        before, rest = text.split(START)
        new = before + block + rest.split(END, 1)[1]
    elif START in text or END in text:
        raise ValueError(f"the lil-memory block in {path} is damaged; remove it and try again")
    else:
        new = (text.rstrip() + "\n\n" if text.strip() else "") + block + "\n"
    return path, (write(path, new) if new != text else None)


def installed(client: str, config: Path | None = None) -> dict | None:
    """Our server entry in the client's config, if any."""
    config = config or CLIENTS[client]()
    try:
        return load(config)[1].get(servers_key(config), {}).get(SERVER_NAME)
    except (OSError, ValueError):
        return None
