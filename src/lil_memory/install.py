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


def install(client: str, vault: Path, config: Path | None = None) -> tuple[Path, Path | None]:
    """Add or replace our server entry, keeping every other key. Returns (config, backup)."""
    config = config or CLIENTS[client]()
    text, data = load(config)  # raises before anything is written
    output = render(config, text, data, entry(client, vault))
    backup = None
    if config.exists():
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup = config.with_name(f"{config.name}.{stamp}.bak")
        shutil.copy2(config, backup)
    config.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{config.name}.", dir=config.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(output)
        if backup:
            shutil.copymode(backup, tmp)
        os.replace(tmp, config)
    except BaseException:
        os.unlink(tmp)
        raise
    return config, backup


def installed(client: str, config: Path | None = None) -> dict | None:
    """Our server entry in the client's config, if any."""
    config = config or CLIENTS[client]()
    try:
        return load(config)[1].get(servers_key(config), {}).get(SERVER_NAME)
    except (OSError, ValueError):
        return None
