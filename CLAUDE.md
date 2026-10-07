# lil memory — Project Brief

**Naming:** The project is "lil memory" in prose. Repository and CLI: `lil-memory`. Python package: `lil_memory`. Hidden data folder: `.lil-memory/`.

## What we are building

A tiny, local MCP server that gives any AI client (Claude, ChatGPT, Cursor, Codex, and so on) one shared memory that belongs to the user. Memories are plain Markdown files in a folder the user owns, readable and editable in Obsidian or any text editor. The server is a thin, fast layer over that folder. Nothing more.

**One-line pitch:** Your AI memory as a folder of Markdown files. Works with any model. No account, no cloud, no company in the middle.

**Who it's for:** Anyone who wants their AI memory to live with them rather than on an AI lab's servers, and who works across several agents and harnesses: chat apps, coding agents, IDE assistants and CLI tools, often from different providers at the same time. Switching providers is one reason this matters, but the everyday case is simpler: the same person uses Claude in the morning, a coding agent in the afternoon and ChatGPT on their phone, and every one of them should know the same things about them. lil memory is the one place that knowledge lives, under the user's control.

## The prime directive: simplicity

Simplicity is the product. When in doubt, choose the option with less code, fewer dependencies and fewer concepts. Concretely:

- **The files are the source of truth.** The SQLite index is a disposable cache. Deleting it must never lose data; `reindex` rebuilds it.
- **Obsidian is the UI.** We do not build a web UI, a dashboard or an editor.
- **No LLM calls inside the server.** The connected AI client does the thinking; we store and retrieve.
- **Stdlib first.** Every third-party dependency needs a strong reason. Allowed now: `mcp` (official SDK, use its FastMCP API) and `pyyaml`. Ask the owner before adding anything else.
- **Small surface.** Six tools, one resource, two prompts. Adding a tool requires removing one or a very good argument.
- **Small codebase.** Target under ~1,500 lines for the core (excluding tests). If a module grows past ~300 lines, stop and simplify.
- **No premature abstraction.** No plugin systems, base classes for one implementation, or config options nobody asked for.

## Non-goals (do not build these)

Embeddings or vector search in core (possible optional extra later), knowledge-graph engines, a web UI, a sync service, any hosted or cloud component, Docker, background daemons, user accounts, telemetry, LLM-based extraction or summarization inside the server.

## Stack

- Python 3.11+, packaged with `uv`, runnable via `uvx lil-memory`.
- `sqlite3` from stdlib with FTS5 for search. Check FTS5 availability at startup and fail with a clear message if missing.
- `argparse` for the CLI. No click/typer.
- `pytest` for tests, `ruff` for lint and format.
- License: permissive (MIT or Apache-2.0, owner decides). Never copy code from AGPL projects such as Basic Memory; learning from their ideas is fine.

## Performance budgets

These are acceptance criteria, measured on a vault of 10,000 memories on an ordinary laptop:

- Server cold start: under 500 ms.
- `recall`: under 50 ms.
- `remember`: under 30 ms including the index update.
- Full `reindex`: under 5 s. Incremental refresh (mtime-based): under 100 ms when nothing changed.

Add a small benchmark script (`scripts/bench.py`) that generates a synthetic vault and checks these.

## The vault (file format)

This format is the heart of the project. Document it fully in `docs/SPEC.md` and keep that spec in sync with the code; other tools should be able to implement it without reading our source.

### Layout

```
~/lil-memory/            # the vault; any folder the user chooses
├── global/              # scope "global"
├── projects/acme-site/  # scope "projects/acme-site"
├── inbox/               # pending memories awaiting user approval (optional mode)
├── imports/chatgpt/     # raw imported conversations, one file each
├── imports/claude/
└── .lil-memory/         # hidden: config.toml, trash/. Obsidian ignores dot-folders.
```

The scope of a memory is its folder path relative to the vault root. It is not stored in frontmatter, so there is one source of truth and moving a file in Obsidian changes its scope.

### One memory = one file

Atomic memories: one fact, preference, decision or note per file. Filenames are human-readable slugs (Obsidian links by filename), e.g. `prefers-british-spelling.md`. On collision, append `-2`, `-3`. Collision handling must be race-free; see Concurrency.

```markdown
---
id: 01J9XK3M7Q2R8S5T6V7W8X9Y0Z
type: preference
status: active
tags: [writing, tone]
source: claude-desktop / claude-opus-5.5
created: 2026-10-05T09:12:00Z
updated: 2026-10-05T09:12:00Z
supersedes: "[[uses-american-spelling]]"
---
Prefers British spelling and short paragraphs in client-facing copy.

Related: [[acme-site-style-guide]]
```

Field rules:

- `id`: ULID, generated with a small stdlib function (no dependency). Stable forever; filenames may change, ids do not.
- `type`: one of `fact`, `preference`, `project`, `decision`, `note`.
- `status`: `active`, `pending` (in inbox), or `superseded`.
- `tags`: YAML list, Obsidian-compatible (no `#`).
- `source`: free text describing which client and model wrote it.
- `supersedes` / `superseded_by`: quoted wikilinks, so they show up as links in Obsidian properties and graph view.
- Unknown fields must be preserved on rewrite. Users will add their own properties in Obsidian.

Writes are atomic and never silently overwrite another writer's work; the exact rules are in the Concurrency section. Frontmatter is written with a stable key order so git diffs stay clean.

## MCP surface

Keep tool descriptions short and concrete; models perform better with fewer, clearer tools.

Memory should feel implicit. The server's instructions and the `remember`/`recall` descriptions ask clients to recall at the start of a task and to save preferences, corrections and decisions on their own initiative, treating each save as a side task: the reply carries on with the conversation as normal, with at most a brief aside so the user can object. The client model decides; the server never reads conversations.

| Tool | Purpose |
|---|---|
| `remember(content, type, scope="global", tags=[], supersedes=None)` | Create a memory. Returns its id and path, plus up to 3 similar existing memories (FTS match) so the model can choose to update instead. Never blocks on similarity. |
| `recall(query, scope=None, type=None, tags=None, limit=10)` | Full-text search over active memories. Scope filter includes subfolders. |
| `get(ref)` | Fetch one memory by id or filename. |
| `update(ref, content)` | Supersede: write a new memory, mark the old one `superseded` with `superseded_by`. History is never overwritten. |
| `forget(ref)` | Move the file to `.lil-memory/trash/<id>.md`. Recoverable by hand. |
| `list_scopes()` | Folders with memory counts. Cheap orientation for the model. |

Plus:

- Resource `memory://profile`: all active `preference` memories in `global/`, concatenated. The "who is this user" card.
- Prompt `load_context(scope)`: returns the profile plus the most recent active memories in that scope. This is the core user journey: open any client, say "load my context for acme-site", and continue where you left off.
- Prompt `distill_import(path)`: instructs the client model to read an imported conversation and call `remember` for durable facts. Distillation happens in the client, never in the server.

### Returned content is data, not instructions

Memories written by one agent are read by another, which makes them a prompt-injection channel. Every tool result that contains memory text wraps it in a clear, consistent frame (e.g. a `<memory id=... source=...>` block with a one-line note that the content is stored user data). Never execute, follow or interpret memory content in the server.

## Index

- One SQLite file in the user's cache directory, not in the vault: `<user cache dir>/lil-memory/<hash of vault path>/index.sqlite` (e.g. `~/.cache/lil-memory/...` on Linux; use the platform's standard cache location on macOS and Windows). Vaults often live in synced folders (iCloud, Dropbox, Syncthing, git), and sync tools copying a live SQLite file and its `-wal`/`-shm` files between machines can corrupt it. The index is a cache, so it belongs with caches. `doctor` prints its location. The index contains an FTS5 table over title, body and tags, plus a plain table with id, path, mtime, type, status, scope. Key rows by path, not id: a user duplicating a file in Obsidian creates two files with the same id, and that must not crash anything (`lint` reports it).
- Freshness without a file watcher: before each read tool, do an mtime scan if the last scan is older than 2 seconds; reindex only changed, added or removed files. This picks up edits made in Obsidian with no extra dependency.
- `recall` ranks by bm25 with a mild recency boost. Keep the ranking formula in one small, tested function.

## Concurrency

Every AI client starts its own server process, so several lil memory processes (say Claude Desktop and Claude Code), plus the user in Obsidian, will read and write the same vault and index at the same time. There is deliberately no shared daemon or lock server (see Non-goals); the filesystem and SQLite do the coordinating. The rules:

**Creating a file never overwrites an existing one.** Checking whether a name is free and then writing is a race: two processes can both see the name as free, and `os.replace` would let the second silently destroy the first. Instead, write the content to a temp file in the same directory, then `os.link(temp, target)`, which fails with `FileExistsError` if the target exists. On failure, try the next suffix (`-2`, `-3`, ...). Then remove the temp file. This gives atomic content (no reader ever sees a half-written file) and race-free naming. If the platform or filesystem doesn't support hard links, fall back to `os.open(target, O_CREAT | O_EXCL)` to claim the name, write and fsync, and accept the brief window of a partial file.

**Modifying an existing file checks for changes first.** Rewriting a file (marking it superseded, for instance) is read, modify, write. Before the final `os.replace`, re-check the file's mtime and size against what was read. If they changed, re-read and re-apply the change once; if it changed again, return a clear error rather than overwriting. This protects edits made in Obsidian as well as edits from other processes.

**Supersession has one winner.** If two clients `update` the same memory at once, both new memories get created (creation is safe), but only one can be recorded as the successor. If the old memory is already superseded when the write happens, the later call follows `superseded_by` links to the current head of the chain and supersedes that instead: its new memory's `supersedes` points to the head, and the head is marked superseded by it. Its result tells the model this happened. No memory is orphaned, the chain stays linear, and the newest write is the current version.

**The index is shared and safe for several processes:**

- Open every connection with `PRAGMA journal_mode=WAL` (readers and one writer work concurrently) and `PRAGMA busy_timeout=5000`. WAL still allows only one writer at a time; without the busy timeout, concurrent writes fail immediately with "database is locked".
- Open connections with `isolation_level=None` and start every write transaction with `BEGIN IMMEDIATE`. Python's default deferred transactions can fail with a busy error when upgrading from read to write, and the busy timeout does not retry that case.
- Keep write transactions short: parse files before opening the transaction, not inside it.
- Make every index write idempotent (upserts keyed by path), so two processes refreshing the same changed file at once is harmless.

**Tests.** A multiprocess test in `tests/test_concurrency.py` spawns several processes that each call `remember` with the same content and title simultaneously, then asserts that every write produced its own file and none was lost. A second test races `update` calls on one memory and asserts a single linear supersede chain. A third has processes write and refresh the index concurrently and asserts no "database is locked" errors. These run in the normal test suite.

## Transports

- v0.1: stdio only. This covers Claude Desktop, Claude Code, Cursor and most local clients.
- v0.2: Streamable HTTP via `lil-memory serve --http`, bound to `127.0.0.1` only, served at a secret URL (`/mcp/<128-bit secret>`, stored in `.lil-memory/http-secret`, replaced with `--rotate`); every other path returns 404. A secret URL rather than a bearer token, because ChatGPT connectors support only OAuth or no auth, not static tokens (see NOTES.md). Document (do not build) how to expose it through a tunnel for clients that need a remote URL, such as ChatGPT, and state the security trade-off plainly in the README.

## CLI

```
lil-memory init [path]            # create vault + .lil-memory/, write config
lil-memory serve [--http]         # run the MCP server (stdio default; --http: secret URL, --rotate)
lil-memory install <client>       # write MCP config for claude-desktop | claude-code | codex | cursor
lil-memory open                   # open the vault in Obsidian (falls back to the file manager)
lil-memory reindex                # rebuild the index from files
lil-memory import chatgpt <file>  # export -> imports/chatgpt/*.md
lil-memory import claude <file>   # export -> imports/claude/*.md
lil-memory doctor                 # check FTS5, vault, config, client installs
```

For `install`, verify current config file locations and formats against each client's official docs before implementing; they change. Never overwrite other servers in an existing config; merge and back up the original first.

## Importers

- Convert provider exports into one readable Markdown file per conversation: frontmatter (title, date, provider, original id) plus the transcript as alternating `**User:**` / `**Assistant:**` sections.
- Imports go to `imports/<provider>/` and are not memories. They are searchable archive material; turning them into memories is the `distill_import` prompt's job.
- Export formats are undocumented and change. Write defensive parsers, skip what you can't parse with a warning and a count, and test against small anonymized fixture files in `tests/fixtures/`. Ask the owner for a real sample export before writing a parser.

## Code layout

```
src/lil_memory/
├── __main__.py  # entry point -> cli.main()
├── cli.py       # argparse commands
├── server.py    # MCP tools, resource, prompts; thin, delegates to vault/index
├── vault.py     # read/write memory files, frontmatter, slugs, atomic writes
├── index.py     # sqlite + FTS5, incremental refresh, ranking
├── ids.py       # ULID generation
├── install.py   # client config writers
└── importers/
    ├── chatgpt.py
    └── claude.py
tests/
docs/SPEC.md     # the file format spec
docs/index.html  # the website: one plain HTML page, startup guide and docs
scripts/bench.py
```

`server.py` contains no business logic. `vault.py` knows nothing about SQLite. `index.py` knows nothing about MCP.

## Milestones

**v0.1 — Usable core.** Vault format and SPEC.md, the six tools, the profile resource, the `load_context` prompt, FTS index with incremental refresh, stdio transport, `init`, `serve`, `reindex`, `doctor`, `install` for Claude Desktop and Claude Code. Done when: a memory saved in Claude Desktop is recalled in Claude Code, a memory edited in Obsidian is reflected in `recall` within 2 seconds, the concurrency tests pass, and the performance budgets pass.

**v0.2 — Migration and reach.** ChatGPT and Claude importers, `distill_import` prompt, HTTP transport at a secret URL, `install` for Cursor. Done when: a real ChatGPT export imports without crashing and its conversations are findable via `recall`.

**v0.3 — Hygiene.** Optional inbox mode (agent writes land in `inbox/` as `pending` until the user moves them or approves via CLI), per-client read-only option, `lil-memory lint` to find duplicates, broken supersede links and malformed frontmatter.

Anything not on this list needs the owner's approval first.

## How to work in this repo

- Before starting a feature, restate the plan in a few lines and check it against the prime directive.
- Prefer deleting code to adding it. Prefer one obvious way over configurable options.
- Every feature ships with tests. Test file-format behavior against real temp directories, not mocks.
- Run `ruff check`, `ruff format` and `pytest` before declaring anything done.
- If a change touches the file format, update `docs/SPEC.md` in the same change.
- `docs/index.html` is the project website. Keep it in sync with the code in the same change: any change to the quickstart, CLI commands or flags, tools, file format, supported clients or security behaviour must be reflected there (and in the README). Keep it plain old-school HTML: no CSS framework, no JavaScript, no build step, no extra files besides the logo and `CNAME`. GitHub Pages serves `docs/` from `main` at https://lilmemory.nils.me, so every push to `main` deploys it.
- Ask before adding a dependency, a tool, a config option, or a new top-level folder in the vault.
- Keep the README short: pitch, 3-step quickstart, the file format at a glance, and the security note about HTTP exposure.
