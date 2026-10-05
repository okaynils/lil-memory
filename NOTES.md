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
