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

## Benchmark results (M-series MacBook, 2026-10-05)

| Budget | Limit | Measured |
|---|---|---|
| Full reindex of 10k | 5 s | 0.9 s |
| Refresh, no change | 100 ms | 52–80 ms |
| `recall` (p95, warm) | 50 ms | 4–6 ms |
| `remember` (p95) | 30 ms | 17–20 ms |
| Cold start | 500 ms | about 290 ms |

- **Scan cost on the first recall.** A `recall` that arrives after the 2-second window also runs the mtime scan, and then takes about 90 ms. The brief budgets recall and refresh separately, so this passes, but the first recall after a pause pays for the scan.
- **Where the scan time goes.** Nearly all of it is per-file `stat()` calls. Running them in threads was slower on APFS.
- **If this needs to get faster:** move the scan off the request path, for example by running it right after each reply, or watch directories with kqueue or inotify. The brief rules out daemons and extra dependencies, so neither is done here.

## HTTP mode: secret URL instead of a bearer token (owner's decision, 2026-10-05)

- **Why the brief's plan doesn't work.** The brief planned "HTTP bound to 127.0.0.1, requiring a bearer token generated at `init`". But ChatGPT connectors support only OAuth 2.1 or no authentication. OpenAI's docs say static API keys and bearer tokens are not supported: https://developers.openai.com/plugins/build/auth
- **The two options.** A minimal built-in OAuth server would be about 200 lines, with stored clients and tokens plus a consent page. A secret URL is about 30 lines. The owner chose the secret URL as the smallest solution that fits the prime directive.
- **What `serve --http` does:**
  - Serves MCP only at `/mcp/<secret>` on `127.0.0.1`; every other path returns 404. The secret is 128 bits and lives in `.lil-memory/http-secret`, with mode 0600 and listed in `.lil-memory/.gitignore`.
  - `--rotate` replaces the secret. `--port` sets the port, which defaults to 8765.
  - DNS-rebinding protection is off. It would reject the tunnel's `Host` header, and a rebinding attack would still need the secret path.
  - Logging is at WARNING, so uvicorn's access log never prints the URL.
- **Secret creation.** The secret is created on the first `serve --http`, not at `init`, so people who never use HTTP never have a credential lying around.
- **Not done.** No real ChatGPT end-to-end check has been run. The tests use the MCP SDK's HTTP client and raw requests that carry a tunnel `Host` header.
- **Later.** If the threat model ever calls for it, OAuth can be added alongside the secret URL without breaking it.
