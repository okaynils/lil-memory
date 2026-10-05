"""Register the lil memory MCP server in a client's JSON config, merging with what is there."""

import json
import os
import shutil
import sys
import tempfile
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


CLIENTS = {"claude-desktop": claude_desktop_config, "claude-code": claude_code_config}


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


def load(path: Path) -> dict:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    data = json.loads(text) if text.strip() else {}
    if not isinstance(data, dict) or not isinstance(data.get("mcpServers", {}), dict):
        raise ValueError(f"{path} does not look like an MCP client config; not touching it")
    return data


def install(client: str, vault: Path, config: Path | None = None) -> tuple[Path, Path | None]:
    """Add or replace our server entry, keeping every other key. Returns (config, backup)."""
    config = config or CLIENTS[client]()
    data = load(config)  # raises before anything is written
    backup = None
    if config.exists():
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup = config.with_name(f"{config.name}.{stamp}.bak")
        shutil.copy2(config, backup)
    data.setdefault("mcpServers", {})[SERVER_NAME] = entry(client, vault)
    config.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{config.name}.", dir=config.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        if backup:
            shutil.copymode(backup, tmp)
        os.replace(tmp, config)
    except BaseException:
        os.unlink(tmp)
        raise
    return config, backup


def installed(client: str, config: Path | None = None) -> dict | None:
    """Our server entry in the client's config, if any."""
    try:
        return load(config or CLIENTS[client]()).get("mcpServers", {}).get(SERVER_NAME)
    except (OSError, ValueError):
        return None
