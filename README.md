<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/lil-memory-dark.svg">
    <img src="docs/lil-memory-light.svg" alt="lil memory" width="50%">
  </picture>
</p>

# lil memory

Your AI memory as a folder of Markdown files. Works with any model. No account, no cloud, no company in the middle.

lil memory is a small local [MCP](https://modelcontextprotocol.io) server. Every AI client you connect (Claude Desktop, Claude Code, Codex, ChatGPT and others) reads and writes the same memories. The memories are plain files in a folder you own, so you can read and edit them in [Obsidian](https://obsidian.md) or any text editor.

## Why lil memory

Most people use more than one AI, and each keeps its own memory on its own company's servers. lil memory is one memory they all share, and it lives with you.

- **Yours, as plain files.** One Markdown file per memory, in a folder you own. Nothing to export, nothing to be locked into.
- **Every AI, the same memory.** Claude, Codex, ChatGPT and any other MCP client, at the same time.
- **Obsidian is the interface.** Memories are linked notes you can browse and fix by hand.
- **Small on purpose.** One Python program, two dependencies, no account, no cloud, no API keys, no AI model of its own. About 1,400 lines.

| | Where your memory lives | Shared across AIs | What you need |
|---|---|---|---|
| Built-in memory (ChatGPT, Claude, Gemini) | The provider's servers | No; imports are one-time copies | Nothing |
| Hosted memory services | The service's cloud | Yes | An account and an API key |
| Self-hosted stacks, such as OpenMemory | Databases on your machine | Yes | Docker, Postgres, a vector database, an LLM API key |
| Reference MCP memory server | One JSON Lines file | Yes | Node.js |
| Basic Memory | Markdown files on your machine | Yes | A Python program (AGPL) |
| **lil memory** | **Markdown files on your machine** | **Yes** | **One small Python program (MIT)** |

Basic Memory is the closest relative; pick it if you want a fuller knowledge-management system. lil memory uses full-text search, not embeddings, and is built for one person, not a team. More on [the website](https://lilmemory.nils.me/#why).

## Quickstart

```sh
uvx lil-memory init                      # 1. create the vault at ~/lil-memory
uvx lil-memory install claude-desktop    # 2. connect a client (or: claude-code, codex)
```

3. Restart the client and talk to it:
   - "Remember that I prefer British spelling."
   - "Load my context for acme-site."

   Usually you don't need to ask: connected AIs recall what's relevant and save your preferences, corrections and decisions on their own, without derailing the conversation.

Run `lil-memory open` to explore and edit your memories in Obsidian.

For ChatGPT and any other MCP client, see [Connect your AI](https://lilmemory.nils.me/connect.html).

`lil-memory doctor` checks your setup. `lil-memory reindex` rebuilds the search index from the files. Use `--vault PATH` or `$LIL_MEMORY_VAULT` to keep the vault somewhere else.

## The format at a glance

One memory is one Markdown file. Its folder is its scope.

```
~/lil-memory/
├── global/prefers-british-spelling.md
├── projects/acme-site/deploys-to-cloudflare.md
└── .lil-memory/          # config and trash
```

```markdown
---
id: 01J9XK3M7Q2R8S5T6V7W8X9Y0Z
type: preference
status: active
tags:
  - writing
source: claude-desktop 1.0
created: 2026-10-05T09:12:00Z
updated: 2026-10-05T09:12:00Z
---
Prefers British spelling and short paragraphs in client-facing copy.
```

- Updating a memory never overwrites it. The old file is marked `superseded` and links to the new one.
- Forgetting a memory moves it to `.lil-memory/trash/`.
- The full format is in [docs/SPEC.md](docs/SPEC.md). Other tools can implement it without reading this code.

The connected AI exposes six tools: `remember`, `recall`, `get`, `update`, `forget` and `list_scopes`. It also has a `memory://profile` resource and a `load_context` prompt.

## Security

- **Local by default.** Claude Desktop, Claude Code and Codex start lil memory as a local process and talk to it over stdio. Nothing listens on the network.
- **Memories are untrusted input.** Memories written by one AI are read by another, so stored text could try to give instructions. lil memory returns memories inside clearly marked `<memory>` blocks, labeled as data and not instructions. It never acts on their content itself.
- **Remote clients such as ChatGPT** can only reach a server through an HTTPS URL, and ChatGPT can't send a static token. So HTTP mode keeps things minimal: the URL itself is the secret. Use it with caution, because anyone who has the URL gets full access to your memories (see the trade-off below).

  ```sh
  lil-memory serve --http                         # prints http://127.0.0.1:8765/mcp/<secret>
  cloudflared tunnel --url http://127.0.0.1:8765  # or any other tunnel
  ```

  In ChatGPT, turn on developer mode in the settings, then add `https://<tunnel-host>/mcp/<secret>` as a custom connector with no authentication.

  **The trade-off:** anyone who has that URL can read, change and forget all of your memories. A URL leaks more easily than a password, for example through screenshots, shell history or the tunnel provider. OpenAI also stores it in your connector settings, and sees every memory ChatGPT reads. HTTP mode only runs while you keep `serve --http` open. `lil-memory serve --http --rotate` replaces the secret, and the old URL stops working immediately. It is good practice to rotate the URL once in a while, and right away if you think it has leaked. To keep HTTP mode running on your laptop or an always-on machine, see [Run it all the time](https://lilmemory.nils.me/connect.html#always-on).

## License

MIT
