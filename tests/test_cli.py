import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from lil_memory import __version__, cli, install, vault
from lil_memory.cli import main
from lil_memory.index import index_path


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    """Every test gets its own HOME, so real client configs are never read or written."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData"))
    monkeypatch.delenv("LIL_MEMORY_VAULT", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert Path.home() == home
    for client in install.CLIENTS.values():
        assert client().is_relative_to(home)
    assert cli.obsidian_config().is_relative_to(home)
    return home


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"lil-memory {__version__}"


def test_no_command_prints_help(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "usage: lil-memory" in out
    for command in ["init", "serve", "install", "open", "reindex", "doctor"]:
        assert command in out


def test_module_entry_point():
    result = subprocess.run(
        [sys.executable, "-m", "lil_memory", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == f"lil-memory {__version__}"


def test_init_creates_vault(tmp_path, capsys):
    root = tmp_path / "vault"
    assert main(["init", str(root)]) == 0
    assert (root / "global").is_dir()
    assert (root / ".lil-memory/config.toml").read_text() == 'format = "0.1"\n'
    assert (root / ".lil-memory/.gitignore").read_text() == "http-secret\n"
    assert index_path(root).exists()
    assert not (root / ".lil-memory/index.sqlite").exists()
    assert f"Vault ready at {root.resolve()} (0 memories)" in capsys.readouterr().out


def test_init_on_existing_notes_keeps_them_and_config(tmp_path, capsys):
    root = tmp_path / "vault"
    (root / "notes").mkdir(parents=True)
    (root / "notes/idea.md").write_text("An idea")
    (root / ".lil-memory").mkdir()
    (root / ".lil-memory/config.toml").write_text('format = "0.1"\nmine = true\n')
    assert main(["init", str(root)]) == 0
    assert (root / "notes/idea.md").read_text() == "An idea"
    assert "mine = true" in (root / ".lil-memory/config.toml").read_text()
    assert "(1 memories)" in capsys.readouterr().out


def test_vault_from_option_and_environment(tmp_path, monkeypatch, fake_home):
    assert main(["init", "--vault", str(tmp_path / "a")]) == 0
    assert (tmp_path / "a/.lil-memory").is_dir()
    monkeypatch.setenv("LIL_MEMORY_VAULT", str(tmp_path / "b"))
    assert main(["init"]) == 0
    assert (tmp_path / "b/.lil-memory").is_dir()
    monkeypatch.delenv("LIL_MEMORY_VAULT")
    assert main(["init"]) == 0
    assert (fake_home / "lil-memory/.lil-memory").is_dir()


def test_commands_require_a_vault(tmp_path, capsys):
    for command in ["serve", "reindex", "install"]:
        argv = [command, "--vault", str(tmp_path / "missing")]
        if command == "install":
            argv.insert(1, "claude-code")
        assert main(argv) == 1
        assert "run `lil-memory init" in capsys.readouterr().err
    assert not (tmp_path / "missing").exists()


def test_reindex(tmp_path, capsys):
    root = tmp_path / "vault"
    main(["init", str(root)])
    vault.create(root, "One", "fact")
    vault.create(root, "Two", "fact")
    assert main(["reindex", "--vault", str(root)]) == 0
    assert "Indexed 2 memories" in capsys.readouterr().out


def test_doctor_passes_on_a_fresh_vault(tmp_path, capsys):
    root = tmp_path / "vault"
    main(["init", str(root)])
    capsys.readouterr()
    assert main(["doctor", "--vault", str(root)]) == 0
    out = capsys.readouterr().out
    assert "FAIL" not in out
    assert "ok    SQLite has FTS5" in out
    assert "note  claude-desktop: not installed" in out
    assert f"ok    index at {index_path(root)}" in out
    assert "old index" not in out


def test_doctor_notes_an_old_index_in_the_vault(tmp_path, capsys):
    root = tmp_path / "vault"
    main(["init", str(root)])
    (root / ".lil-memory/index.sqlite").write_bytes(b"")
    capsys.readouterr()
    assert main(["doctor", "--vault", str(root)]) == 0
    assert "note  old index in the vault is unused" in capsys.readouterr().out


def test_doctor_fails_without_a_vault(tmp_path, capsys):
    assert main(["doctor", "--vault", str(tmp_path / "nowhere")]) == 1
    assert "FAIL  no vault at" in capsys.readouterr().out


def test_doctor_notes_malformed_files_and_installs(tmp_path, capsys):
    root = tmp_path / "vault"
    main(["init", str(root)])
    (root / "global/bad.md").write_text("---\n[oops\n---\nbody")
    main(["install", "claude-code", "--vault", str(root)])
    capsys.readouterr()
    assert main(["doctor", "--vault", str(root)]) == 0
    out = capsys.readouterr().out
    assert "note  1 memories have malformed frontmatter" in out
    assert "ok    claude-code: installed" in out
    assert "note  codex: not installed (lil-memory install codex)" in out


@pytest.mark.parametrize("client", ["claude-desktop", "claude-code"])
def test_install_into_fresh_config(tmp_path, client, capsys):
    root = tmp_path / "vault"
    main(["init", str(root)])
    assert main(["install", client, "--vault", str(root)]) == 0
    config = install.CLIENTS[client]()
    data = json.loads(config.read_text())
    entry = data["mcpServers"]["lil-memory"]
    assert entry["args"][-3:] == ["serve", "--vault", str(root.resolve())]
    assert ("type" in entry) == (client == "claude-code")
    assert "backup" not in capsys.readouterr().out


def test_install_merges_and_backs_up(tmp_path):
    config = tmp_path / "home/.claude.json"
    original = {
        "numStartups": 7,
        "projects": {"/x": {"mcpServers": {"proj": {}}}},
        "mcpServers": {"other": {"type": "stdio", "command": "other-server", "args": []}},
    }
    config.write_text(json.dumps(original))
    path, backup = install.install("claude-code", tmp_path / "vault")
    assert path == config
    data = json.loads(config.read_text())
    assert data["numStartups"] == 7
    assert data["projects"] == original["projects"]
    assert data["mcpServers"]["other"] == original["mcpServers"]["other"]
    assert data["mcpServers"]["lil-memory"]["command"]
    assert json.loads(backup.read_text()) == original
    assert backup.parent == config.parent
    # Installing again replaces only our entry.
    install.install("claude-code", tmp_path / "vault2")
    data = json.loads(config.read_text())
    assert data["mcpServers"]["lil-memory"]["args"][-1] == str(tmp_path / "vault2")
    assert set(data["mcpServers"]) == {"other", "lil-memory"}


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", '{"mcpServers": []}'])
def test_install_refuses_to_touch_unexpected_files(tmp_path, content, capsys):
    root = tmp_path / "vault"
    main(["init", str(root)])
    config = tmp_path / "home/.claude.json"
    config.write_text(content)
    assert main(["install", "claude-code", "--vault", str(root)]) == 1
    assert config.read_text() == content
    assert list(config.parent.iterdir()) == [config]
    capsys.readouterr()


def test_claude_code_respects_claude_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    path, _ = install.install("claude-code", tmp_path / "vault")
    assert path == tmp_path / "cfg/.claude.json"
    assert install.installed("claude-code")


def test_installed_reads_entry(tmp_path):
    assert install.installed("claude-code") is None
    install.install("claude-code", tmp_path / "vault")
    assert install.installed("claude-code")["args"][-1] == str(tmp_path / "vault")


def test_server_command_uses_uvx_only_from_its_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "executable", "/home/u/.cache/uv/archive-v0/abc/bin/python")
    assert install.server_command(tmp_path) == (
        "uvx",
        ["lil-memory", "serve", "--vault", str(tmp_path)],
    )
    monkeypatch.setattr(sys, "executable", "/home/u/.local/share/uv/tools/lil-memory/bin/python")
    command, args = install.server_command(tmp_path)
    assert command.endswith("bin/python") and args[:2] == ["-m", "lil_memory"]


def test_serve_speaks_mcp_over_stdio(tmp_path):
    root = tmp_path / "vault"
    main(["init", str(root)])
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    result = subprocess.run(
        [sys.executable, "-m", "lil_memory", "serve", "--vault", str(root)],
        input="".join(json.dumps(m) + "\n" for m in messages),
        capture_output=True,
        text=True,
        timeout=30,
    )
    replies = [json.loads(line) for line in result.stdout.splitlines()]
    assert replies[0]["result"]["serverInfo"]["name"] == "lil-memory"
    assert len(replies[1]["result"]["tools"]) == 6


CODEX_CONFIG = """# my settings
model = "gpt-6-sol"
notify = ["a", "b"]

[projects."/Users/me"]
trust_level = "trusted"

[mcp_servers.other]
command = "other-server"
args = ["--flag"]

[mcp_servers.lil-memory]
command = "old-python"
args = ["serve"]

[mcp_servers.lil-memory.env]
OLD = "1"

[[profiles.list]]
name = "x"
"""


def test_install_codex_into_fresh_config(tmp_path, capsys):
    root = tmp_path / "vault"
    main(["init", str(root)])
    assert main(["install", "codex", "--vault", str(root)]) == 0
    config = tmp_path / "home/.codex/config.toml"
    assert config.read_text().startswith("[mcp_servers.lil-memory]\ncommand = ")
    entry = tomllib.loads(config.read_text())["mcp_servers"]["lil-memory"]
    assert entry == {"command": entry["command"], "args": entry["args"]}
    assert entry["args"][-3:] == ["serve", "--vault", str(root.resolve())]
    assert install.installed("codex") == entry
    capsys.readouterr()


def test_install_codex_replaces_only_our_table_and_keeps_comments(tmp_path):
    config = tmp_path / "home/.codex/config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(CODEX_CONFIG)
    path, backup = install.install("codex", tmp_path / "vault")
    assert backup.read_text() == CODEX_CONFIG
    text = config.read_text()
    assert text.startswith("# my settings\n")
    assert "old-python" not in text and "OLD" not in text
    data = tomllib.loads(text)
    before = tomllib.loads(CODEX_CONFIG)
    assert data["model"] == "gpt-6-sol"
    assert data["projects"] == before["projects"]
    assert data["profiles"] == before["profiles"]
    assert data["mcp_servers"]["other"] == before["mcp_servers"]["other"]
    assert data["mcp_servers"]["lil-memory"]["args"][-1] == str(tmp_path / "vault")
    # Installing again is stable: one table, pointing at the new vault.
    install.install("codex", tmp_path / "vault2")
    text = config.read_text()
    assert text.count("[mcp_servers.lil-memory]") == 1
    assert install.installed("codex")["args"][-1] == str(tmp_path / "vault2")


@pytest.mark.parametrize(
    "content",
    [
        "not = [valid",  # unparsable
        "mcp_servers = 3\n",  # wrong shape
        '[mcp_servers]\nlil-memory = { command = "x" }\n',  # defined in a way we can't edit
    ],
)
def test_install_codex_refuses_to_touch_unexpected_files(tmp_path, content, capsys):
    config = tmp_path / "home/.codex/config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(content)
    root = tmp_path / "vault"
    main(["init", str(root)])
    assert main(["install", "codex", "--vault", str(root)]) == 1
    assert config.read_text() == content
    assert list(config.parent.iterdir()) == [config]
    capsys.readouterr()


def test_codex_respects_codex_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "cx"))
    path, _ = install.install("codex", tmp_path / "vault")
    assert path == tmp_path / "cx/config.toml"
    assert install.installed("codex")


@pytest.fixture
def launched(monkeypatch):
    """Record what `open` would hand to the operating system instead of opening it."""
    targets = []
    monkeypatch.setattr(cli, "launch", targets.append)
    return targets


def write_obsidian_vaults(vaults: dict) -> None:
    config = cli.obsidian_config()
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({"vaults": vaults}))


def test_open_goes_straight_to_a_known_vault(tmp_path, launched, capsys):
    root = tmp_path / "vault"
    main(["init", str(root)])
    other = {"path": str(tmp_path / "notes"), "ts": 1}
    write_obsidian_vaults({"aaa": other, "b c/1": {"path": str(root), "ts": 2, "open": True}})
    capsys.readouterr()
    assert main(["open", "--vault", str(root)]) == 0
    assert launched == ["obsidian://open?vault=b%20c%2F1"]
    assert f"Opened {root.resolve()} in Obsidian." in capsys.readouterr().out


def test_open_explains_the_one_time_step_for_a_new_vault(tmp_path, launched, capsys):
    root = tmp_path / "vault"
    main(["init", str(root)])
    write_obsidian_vaults({"aaa": {"path": str(tmp_path / "notes"), "ts": 1}})
    capsys.readouterr()
    assert main(["open", "--vault", str(root)]) == 0
    assert launched == ["obsidian://open"]
    out = capsys.readouterr().out
    assert '"Open folder as vault"' in out
    assert str(root.resolve()) in out


@pytest.mark.parametrize("content", [None, "{broken", '{"vaults": []}'])
def test_open_falls_back_to_the_folder_without_obsidian(tmp_path, launched, capsys, content):
    root = tmp_path / "vault"
    main(["init", str(root)])
    if content is not None:
        cli.obsidian_config().parent.mkdir(parents=True)
        cli.obsidian_config().write_text(content)
    capsys.readouterr()
    assert main(["open", "--vault", str(root)]) == 0
    assert launched == [str(root.resolve())]
    assert "obsidian.md" in capsys.readouterr().out


def test_open_needs_a_vault(tmp_path, launched, capsys):
    assert main(["open", "--vault", str(tmp_path / "nowhere")]) == 1
    assert launched == []
    assert "no vault at" in capsys.readouterr().err


def test_open_reports_a_failing_launcher(tmp_path, monkeypatch, capsys):
    root = tmp_path / "vault"
    main(["init", str(root)])

    def broken(target):
        raise OSError("no opener")

    monkeypatch.setattr(cli, "launch", broken)
    assert main(["open", "--vault", str(root)]) == 1
    assert "could not open" in capsys.readouterr().err
