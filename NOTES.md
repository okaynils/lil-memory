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
