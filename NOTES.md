# Notes

Decisions and observations made during the v0.1 build that are worth a second look.

## Pinned `mcp<2`

- mcp 2.3 takes about 440 ms just to import `mcp.server.fastmcp` on an M-series laptop. Much of that time goes to two copies of the protocol types and the client package, which `mcp/__init__` always imports.
- That leaves almost no room in the 500 ms cold-start budget.
- mcp 1.30 imports in about 290 ms, and FastMCP is its native API. In 2.x, FastMCP is a compatibility shim over `MCPServer`.
- Revisit this when 2.x gets lighter, or if a client needs a protocol version that only 2.x speaks.

## Spec open questions resolved

- `type: project` is kept as the brief has it, with a defined meaning: an overview of a piece of work.
- `.lil-memory/config.toml` holds only `format = "0.1"`. `doctor` uses it to recognize a vault.
- Slugs for non-Latin text, and the two concurrency edge cases, are listed as known limitations in SPEC.md §13.

## `frontmatter.py` split out of `vault.py`

- `vault.py` reached about 400 lines, which is over the brief's ~300-line limit for a module.
- The frontmatter format is a self-contained unit (failsafe YAML parsing, canonical emitting and timestamps), so it moved to `frontmatter.py`. The CLAUDE.md layout doesn't list that module.
- `vault.py` still owns everything about files and operations, and still knows nothing about SQLite.

## Hard links are required for new files

- New memories are placed with `link(2)` from a temporary file, so a concurrent writer can never be overwritten (SPEC §9).
- Filesystems without hard links (FAT/exFAT) are not supported. A fallback would have added code for a rare setup.
- APFS, ext4, btrfs and NTFS all work.

## Test corrections

These were mistakes in tests while they were being written. No assertion was loosened.

- `test_index.py::test_scopes_and_stats` expected 6 memories, but the setup creates 5: one, two, three (superseded), four, and broken.
- `test_index.py::test_duplicate_ids_belong_to_the_first_path` copied into a `global/` folder that didn't exist yet.
- `test_server.py::test_remember_reports_similar_memories` expected the full 8-word slug, but slugs keep 6 words (SPEC §4.1).
- `test_server.py::test_obsidian_edit_is_recalled_within_two_seconds` checked that "green" was absent from the whole output. The title is the unchanged filename, which still contains "green". The test now checks the body text instead, which is what it was meant to verify.

## CLI and install

- **Finding the vault:** `--vault PATH` on every command, then `$LIL_MEMORY_VAULT`, then `~/lil-memory`. `init [path]` also takes a positional path.
- **Not in v0.1:** `serve --http` and `import` are left out of the CLI until v0.2, rather than shipped as stubs.
- **What `install` registers:** the command it writes is the current Python, run as `python -m lil_memory serve --vault <path>`.
  - The exception is when lil-memory is running from uvx's throwaway cache (`archive-v0`). Then it writes `uvx lil-memory serve ...`, which only works once the package is published on PyPI.
- **Config locations:** checked against the official Claude Code MCP docs (code.claude.com/docs/en/mcp) on 2026-10-05.
  - Claude Code: user scope is the top-level `mcpServers` object in `~/.claude.json`, or in `$CLAUDE_CONFIG_DIR/.claude.json` when that is set. `type: "stdio"` is required.
  - Claude Desktop: `claude_desktop_config.json` under `~/Library/Application Support/Claude` (macOS) or `%APPDATA%\Claude` (Windows), with no `type` key.
  - The Linux Desktop path (`~/.config/Claude`) comes only from third-party sources.
- **Writing configs:** `install` refuses to touch a config that isn't a JSON object with an object-valued `mcpServers`. It writes a timestamped `.bak` next to the original before every change.
  - Claude Code rewrites `~/.claude.json` while it runs, so installing while it is open can lose that write. The CLI tells the user to restart the client.
- **Testing:** `tests/test_cli.py` points `HOME`, `APPDATA` and `CLAUDE_CONFIG_DIR` at a temporary directory for every test, and asserts this before each one.
- **`source`:** the field records the MCP client's `clientInfo` name and version, such as `claude-code 2.1.0`. The server cannot see which model is calling it, so the brief's `claude-desktop / claude-opus-5.5` example cannot be produced as-is.

## Concurrency findings

`tests/test_concurrency.py` runs real processes against one vault at the same moment. It found three bugs:

1. **Index creation race.** On a fresh index opened by several processes at once, `PRAGMA journal_mode = WAL` failed with "database is locked", because changing the journal mode ignores the busy timeout. It now runs only when the mode isn't WAL yet, and retries briefly.
2. **Stray file after a lost update race.** When two `update`s raced on one memory, the loser reported an error but left its new file behind. That file claimed to supersede the old one. The new file is now removed when marking the old memory fails. This is also in SPEC §10.2.
3. **`source()` without a request context.** FastMCP raises `ValueError`, not `AttributeError`, when a tool is called with no request context. `source()` now handles both.

Two writers that pass the "is it still active?" check at the same moment can both succeed. That is the known limitation in SPEC §13, and the test accepts it explicitly.
