# lil memory vault format

**Version 0.1 (draft).** This document defines the on-disk format of a lil memory vault. It is meant to be complete: you should be able to build a compatible reader or writer from this document alone, without reading the lil memory source.

The key words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY are to be read as described in RFC 2119.

Until version 0.1 is released, this format may still change in incompatible ways.

## 1. Overview

A vault is an ordinary folder of Markdown files. Each memory is one file: YAML frontmatter followed by a Markdown body. A memory's folder is its scope.

- **The files are the source of truth.** All other state, such as the search index, is a cache that can be thrown away and rebuilt from the files.
- **Humans edit the same files.** Users are expected to open the vault in Obsidian or a text editor and create, edit, move, rename and delete files by hand. Implementations MUST tolerate all of this.

This document uses two roles:

- A **reader** is any program that lists, parses, indexes or searches the vault.
- A **writer** is any program that creates, rewrites, moves or deletes files in the vault.

## 2. Vault layout

The vault root is any directory the user chooses. Example:

```
~/lil-memory/
├── global/                 # scope "global" (the default scope)
├── projects/acme-site/     # scope "projects/acme-site"
├── inbox/                  # pending memories awaiting user approval
├── imports/chatgpt/        # imported conversations (not memories)
├── imports/claude/
└── .lil-memory/            # implementation data (not memories)
```

### 2.1 Which files are memories

A file is a **memory file** if all of the following are true:

1. Its name ends in `.md`, compared case-insensitively.
2. It is not directly in the vault root. It is at least one folder deep.
3. No component of its path relative to the root starts with `.`. This excludes `.lil-memory/`, `.obsidian/`, `.git/`, `.trash/`, and also the dot-prefixed temporary files described in §9.
4. The first path component is not `imports`.

Readers MUST ignore every other file and directory. Readers MUST NOT follow symbolic links to directories.

### 2.2 Reserved paths

| Path | Meaning |
|---|---|
| `.lil-memory/` | Implementation data. Non-normative except where §2.3 says otherwise. |
| `imports/` | Imported conversation archives (§11). These files are not memories. |
| `inbox/` | Memories awaiting user approval. The files are memory files, but their default status is `pending` (§6). |

Writers MUST NOT create memory files under `imports/`.

The vault root MAY contain other files, such as a README, and other folders. Readers ignore root-level files.

### 2.3 The `.lil-memory/` folder

| Path | Status |
|---|---|
| `.lil-memory/index.sqlite` (and `-wal`, `-shm`) | A cache. It MAY be deleted at any time and MUST NOT be needed to recover data. |
| `.lil-memory/config.toml` | Marks the folder as a vault. Contains `format = "0.1"`, the version of this spec the vault was created with. Readers MAY use it to recognize a vault and MUST ignore keys they do not know. |
| `.lil-memory/trash/` | Forgotten memories (§10.3). These are user data and can be recovered by hand. |

The index is specific to one machine, so it MUST NOT be synced between machines. When a writer creates `.lil-memory/`, it SHOULD also create `.lil-memory/.gitignore` containing the single line `index.sqlite*`. The trash folder can still be versioned that way.

## 3. Scope

A memory's **scope** is the path of its parent folder relative to the vault root, using `/` separators and no leading or trailing `/`. For example, the scope of `projects/acme-site/uses-tailwind.md` is `projects/acme-site`.

- Scope is never stored inside the file. Moving a file changes its scope.
- `global` is the default scope.
- A **scope filter** `S` matches scope `S` and every scope that starts with `S/`. The filter `projects` matches `projects` and `projects/acme-site`, but not `projects-old`.

When a writer creates a scope folder from user or model input, it MUST reject the scope if any of the following is true:

- it is empty
- it is absolute
- it contains `\`
- it has an empty path component
- any component is `.` or `..`
- any component starts with `.`
- its first component is `imports`.

Writers SHOULD use slug-style components (§4.1), but readers MUST accept any folder name.

## 4. Filenames and titles

A memory's **title** is its filename without the `.md` extension, called the **stem**. There is no title field. Renaming the file changes the title.

### 4.1 Slug algorithm

When a writer creates a memory, it makes the filename from the content:

1. Take the first line of the content that contains at least one letter or digit. If there is none, the slug is `memory`; go to step 7.
2. Normalize the line to Unicode NFKD and remove all combining marks (category `Mn`). For example, `é` becomes `e` and `ä` becomes `a`.
3. Lowercase it.
4. Replace each run of characters outside `[a-z0-9]` with a single `-`. Remove leading and trailing `-`.
5. Keep the first 6 `-`-separated words.
6. If the result is longer than 60 characters, cut it at the last `-` at or before position 60, or at exactly 60 characters if there is no `-` there. Then remove any trailing `-`. If the result is empty, use `memory`.
7. Make the slug unique (§4.2).

Example: `Prefers British spelling and short paragraphs in client-facing copy.` becomes `prefers-british-spelling-and-short-paragraphs`.

Text without Latin letters (for example CJK) reduces to `memory`, `memory-2`, and so on. This is a known limitation of version 0.1.

### 4.2 Uniqueness

Slugs MUST be unique **across the whole vault**, not just within one folder, so that a wikilink `[[stem]]` points to exactly one file. Uniqueness is checked **case-insensitively** against the stems of every `.md` file whose path has no dot-prefixed component. This includes files under `imports/` and files at the root, because Obsidian links to them too.

If `slug` is taken, try `slug-2`, then `slug-3`, and so on, until a free name is found.

Users may still make duplicate stems by hand. Readers MUST handle them (§8).

## 5. File encoding and structure

- Files are UTF-8. Writers MUST NOT write a byte-order mark and MUST use LF line endings.
- Readers MUST accept a leading BOM and CRLF line endings.

A memory file has this structure:

```
---\n
<frontmatter: a YAML block mapping>
---\n
<body>
```

- **Frontmatter** is present only if the first line of the file (after any BOM) is exactly `---`, ignoring trailing spaces or tabs. It ends at the next line that is exactly `---`, with the same allowance. If there is no closing line, the file has no frontmatter and the whole file is body.
- **Body** is everything after the closing delimiter line. For indexing and display, the **content** is the body with leading and trailing whitespace removed.
- When writing, writers MUST emit `---\n`, the frontmatter, `---\n`, then the content followed by exactly one `\n`.

## 6. Frontmatter

### 6.1 Parsing

Readers MUST parse frontmatter using YAML 1.2 **failsafe schema** semantics:

- every scalar is a string
- no scalar is read as a number, boolean, null or timestamp.

So `created: 2026-10-05T09:12:00Z` is the string `"2026-10-05T09:12:00Z"`, and an empty value `key:` is the empty string.

Empty frontmatter, with no lines or only whitespace between the delimiters, is an empty mapping. Otherwise, the frontmatter is **malformed** if it is not valid YAML or if its top level is not a mapping. A file with malformed frontmatter is still a memory:

- all fields take their defaults
- the body is still the text after the closing delimiter.

Writers MUST NOT rewrite a file whose frontmatter is malformed. They MUST report an error instead.

### 6.2 Fields

| Field | Value | Written by writers | Default when missing or invalid |
|---|---|---|---|
| `id` | ULID (§7) | always | none (§7) |
| `type` | `fact`, `preference`, `project`, `decision` or `note` | always | `note` |
| `status` | `active`, `pending` or `superseded` | always | `pending` under `inbox/`, otherwise `active` |
| `tags` | list of tags | if non-empty | empty list |
| `source` | free text: the client and model that wrote the memory, e.g. `claude-desktop / claude-opus-5.5` | SHOULD | empty |
| `created` | timestamp (§6.3) | always | the file's mtime |
| `updated` | timestamp (§6.3) | always | the value of `created` |
| `supersedes` | wikilink (§8.2) to the memory this one replaces | if set | none |
| `superseded_by` | wikilink to the memory that replaced this one | if set | none |

Field names are case-sensitive. Readers MUST lowercase the values of `type` and `status` before comparing them.

**Status meanings:**

- `active`: a current memory. Search returns it.
- `pending`: written by an agent but not yet approved by the user. Search does not return it by default.
- `superseded`: replaced by a newer memory. Search does not return it, but it can still be fetched directly. History is kept, never overwritten.

**Type meanings:**

- `fact`: something true about the user or their world.
- `preference`: how the user likes things done.
- `project`: an overview of a piece of work, such as its goal, stack or current state. The `projects/` folders group memories by project. This type marks the memory that describes the project itself.
- `decision`: a choice that was made, ideally with the reason.
- `note`: anything else. This is also the default.

Status is the only thing that decides whether a memory is active. A memory MAY have `superseded_by` while its status is `active`, for example if the user reverted a change by hand. In that case it is treated as active.

**Tags:**

- Readers accept either a sequence of strings or a single string. A single string is treated as a one-element list.
- Readers drop a leading `#` from each tag.
- Writers MUST write tags without `#` and MUST NOT write tags that contain whitespace.
- `/` is allowed and means a nested tag, as in Obsidian.

**Unknown fields:** users add their own properties in Obsidian. When a writer rewrites a file, it MUST preserve every unknown field, including its value and position (§6.4).

### 6.3 Timestamps

Writers MUST write timestamps in UTC as `YYYY-MM-DDTHH:MM:SSZ`, for example `2026-10-05T09:12:00Z`.

Readers MUST also accept the following formats. Values with no offset are read as UTC.

- any RFC 3339 date-time, including offsets and fractional seconds
- `YYYY-MM-DDTHH:MM:SS` and `YYYY-MM-DDTHH:MM`, which Obsidian writes for date-time properties
- `YYYY-MM-DD`, read as midnight.

Readers MAY also accept other ISO 8601 forms. A value that a reader cannot parse is treated as missing.

Hand edits do not update `updated`. For recency ranking, readers SHOULD therefore use the later of `updated` and the file's mtime.

Note: Obsidian's date-time property editor does not understand the `Z` suffix and shows these fields as text. This is accepted on purpose, so that the timezone is never ambiguous.

### 6.4 Canonical serialization

Writers MUST emit frontmatter as follows, so that rewrites produce small, stable diffs that match how Obsidian itself formats properties.

**Key order:** `id`, `type`, `status`, `tags`, `source`, `created`, `updated`, `supersedes`, `superseded_by`. All unknown keys follow, in the order they appeared in the original file.

**Omission:** optional known fields with empty values are left out.

**Scalars:**

- A scalar is written as a plain (unquoted) YAML scalar if that plain form is syntactically valid and reads back as the same string under the failsafe schema.
- Otherwise it is written double-quoted, escaping only `"`, `\` and control characters.
- So wikilinks are always quoted, because `[[x]]` would otherwise be read as a sequence. ULIDs and timestamps are plain.
- The empty string is written as `""`.

**Sequences** use block style, with each item indented two spaces under its key:

```yaml
tags:
  - writing
  - tone
```

An empty sequence in an unknown field is written as `[]`, and an empty mapping as `{}`.

**Keys** are written plain if they match `[A-Za-z0-9_][A-Za-z0-9_ -]*` and have no trailing space. Otherwise they are written double-quoted.

**Mappings** nested inside unknown fields use block style with two-space indentation.

Unknown fields are kept **by value, not byte-for-byte**. Comments, quoting style and flow style inside the frontmatter may change when a writer rewrites a file. The body is never changed except as an operation in §10 says.

## 7. IDs

`id` is a [ULID](https://github.com/ulid/spec):

- 26 characters of Crockford base32 using the alphabet `0123456789ABCDEFGHJKMNPQRSTVWXYZ`
- 128 bits: the first 48 bits are milliseconds since the Unix epoch, as a big-endian integer, and the remaining 80 bits come from a cryptographically secure random source
- encoded as 26 groups of 5 bits, most significant first, with 2 leading zero bits of padding. As a result, the first character is always `0` to `7`.

Writers MUST write ids in uppercase. Readers MUST compare ids case-insensitively. Ids do not have to be monotonic within one millisecond.

Rules:

- An id never changes. Renaming or moving a file never changes its id.
- A memory file without a valid id (for example a note created in Obsidian) is still a memory. It can be addressed by filename but not by id. Readers MUST NOT add an id to it. A writer that is asked to rewrite such a file (§10) assigns an id at that point.
- If several files have the same id (for example after the user copies a file), the id belongs to the file whose relative path sorts first by Unicode code point. Readers treat every other copy as having no id.

## 8. References

### 8.1 Tool references (`ref`)

When an operation identifies a memory with a free-form reference, readers resolve it in this order:

1. **Id.** If `ref` is a valid ULID and a memory has that id, use that memory.
2. **Normalize.** Remove surrounding `[[` `]]`. Drop anything from the first `|` or `#`. Drop a trailing `.md`.
3. **Path.** If the result contains `/`, it is a path relative to the root, without the extension. Match it against memory files case-insensitively.
4. **Stem.** Otherwise, match it case-insensitively against the stems of all memory files.

If nothing matches, it is an error. If more than one file matches, it is an error that lists the candidates. A tool reference is never resolved silently when it is ambiguous.

### 8.2 Wikilinks

`supersedes`, `superseded_by` and links in the body use Obsidian wikilink syntax: `[[target]]`, `[[target|alias]]` or `[[folder/target]]`.

Writers MUST write the bare stem, for example `"[[uses-american-spelling]]"`. Slugs are unique across the vault (§4.2), so the bare stem points to one file.

Readers resolve a wikilink as follows:

1. Take `target`, which is the text before any `|` or `#`.
2. If it contains `/`, it is a root-relative path, with `.md` implied. Otherwise, collect every `.md` file in the vault whose path has no dot-prefixed component and whose stem matches `target` case-insensitively. This includes imports.
3. If there are several candidates, choose in this order: a file in the same folder as the linking file, then the shortest path, then the path that sorts first by code point.

A link that resolves to nothing is **broken**. A broken link is not an error for readers.

lil memory 0.1 writes these links but never needs to follow them. These rules are for other tools, and for the planned `lint` command.

## 9. Writing files

**Atomic writes.** Every create or rewrite MUST be atomic:

1. Write the full content to a temporary file in the **same directory**, with a name that starts with `.`, such as `.uses-tailwind.md.tmp-8f3a`. Readers ignore dot files (§2.1), so the temporary file is never visible to them.
2. Flush and fsync the temporary file.
3. Move it into place atomically, for example with `os.replace` or `rename(2)`.

If anything fails, the writer removes the temporary file.

**No clobbering.** A writer that creates a new memory MUST NOT overwrite an existing file. Several writer processes can share one vault, because each AI client starts its own server. A writer SHOULD therefore place new files with an operation that fails if the target exists, such as `link(2)` of the temporary file followed by `unlink`. If placement fails, the writer goes to the next suffix in §4.2.

**Minimal writes.** Writers MUST NOT modify files they were not asked to change. Reading or indexing never writes to memory files.

**Lost updates.** When rewriting an existing file, a writer SHOULD read it again immediately before writing. That way a recent edit made in Obsidian is merged into the rewrite rather than overwritten.

## 10. Operations

These operations define how a writer changes the vault. Tool names in parentheses are those of the lil memory MCP server.

### 10.1 Create (`remember`)

1. Validate the scope (§3) and the type.
2. Build the frontmatter:
   - a new id
   - the given `type`
   - `status: active`
   - the given tags and source
   - `created` and `updated` set to the current time.
3. Write `<scope>/<slug>.md` (§4, §9).
4. If the new memory supersedes another one, set `supersedes` on the new file and apply step 3 of §10.2 to the old file.

### 10.2 Supersede (`update`)

To replace memory **old** with new content:

1. Resolve **old**. If its status is `superseded`, it is an error that names the current memory from `superseded_by`. If its frontmatter is malformed, it is an error (§6.1).
2. **Write the new memory first.** It uses the same scope as **old**, copies `type` and `tags` from **old**, and sets `supersedes: "[[<old stem>]]"`. Its slug comes from the new content (§4.1). Otherwise it follows §10.1.
3. **Then rewrite old.** Set `status: superseded`, set `superseded_by: "[[<new stem>]]"` and set `updated` to now. If **old** has no id, give it one. The body is unchanged and unknown fields are preserved.

Because of this order, a crash between steps 2 and 3 leaves two active memories, which is recoverable. It never leaves a superseded memory that points to nothing.

If step 3 fails, the writer deletes the file it wrote in step 2 and reports the error. For example, another writer may have superseded **old** in the meantime, or its frontmatter may have become malformed. That way a failed operation leaves nothing behind that the caller might duplicate by retrying.

### 10.3 Forget (`forget`)

1. Move the file, unchanged, to `.lil-memory/trash/<relative path>` and create folders as needed.
2. If that name is already taken, add `-<YYYYMMDDTHHMMSSZ>` (the current UTC time) before `.md`.

Wikilinks that point to a forgotten memory become broken. To recover the memory, the user moves the file back by hand.

## 11. Import files

> Provisional: importers arrive in lil memory v0.2.

An imported conversation is one Markdown file at `imports/<provider>/<YYYY-MM-DD>-<slug>.md`:

- `<provider>` is `chatgpt` or `claude`.
- The date is the conversation's start date in UTC.
- The slug comes from the title (§4.1). It is unique within the vault.

Import files are searchable archive material, not memories (§2.1).

```markdown
---
title: Planning the Lisbon trip
date: 2025-03-14T18:02:11Z
provider: chatgpt
original_id: 6740c1f2-8a3e-8000-9b1d-2f7e4c0a5d91
---
**User:**

Can you help me plan four days in Lisbon?

**Assistant:**

Sure. Here's a rough outline…
```

- `title`, `date`, `provider` and `original_id` are all written, using the serialization rules in §6.4. `title` is empty if the export has none.
- The body is a sequence of turns. Each turn is `**User:**` or `**Assistant:**` on its own line, then a blank line, then the message text, then a blank line.
- Messages from other roles (system, tool) MAY be left out.
- Message text is copied as is. Attachments and images are replaced with a short placeholder such as `[image omitted]`.

## 12. Examples

### 12.1 A memory

`global/prefers-british-spelling-and-short-paragraphs.md`:

```markdown
---
id: 01J9XK3M7Q2R8S5T6V7W8X9Y0Z
type: preference
status: active
tags:
  - writing
  - tone
source: claude-desktop / claude-opus-5.5
created: 2026-10-05T09:12:00Z
updated: 2026-10-05T09:12:00Z
supersedes: "[[uses-american-spelling]]"
---
Prefers British spelling and short paragraphs in client-facing copy.

Related: [[acme-site-style-guide]]
```

### 12.2 Supersede, before and after

Before, `projects/acme-site/the-acme-site-deploys-to-netlify.md`:

```markdown
---
id: 01J9XJ0000AAAAAAAAAAAAAAAA
type: decision
status: active
tags:
  - hosting
created: 2026-09-01T10:00:00Z
updated: 2026-09-01T10:00:00Z
---
The acme site deploys to Netlify.
```

After `update(ref="the-acme-site-deploys-to-netlify", content="The acme site deploys to Cloudflare Pages since October.")`, the old file `projects/acme-site/the-acme-site-deploys-to-netlify.md` is rewritten:

```markdown
---
id: 01J9XJ0000AAAAAAAAAAAAAAAA
type: decision
status: superseded
tags:
  - hosting
created: 2026-09-01T10:00:00Z
updated: 2026-10-05T11:30:00Z
superseded_by: "[[the-acme-site-deploys-to-cloudflare]]"
---
The acme site deploys to Netlify.
```

And a new file is created, `projects/acme-site/the-acme-site-deploys-to-cloudflare.md`:

```markdown
---
id: 01J9XQ5V2C8N4H7K3M9P6R1T0W
type: decision
status: active
tags:
  - hosting
source: claude-code / claude-opus-5.5
created: 2026-10-05T11:30:00Z
updated: 2026-10-05T11:30:00Z
supersedes: "[[the-acme-site-deploys-to-netlify]]"
---
The acme site deploys to Cloudflare Pages since October.
```

### 12.3 A note written by hand in Obsidian

`projects/acme-site/Client contacts.md`, with no frontmatter:

```markdown
Main contact is Dana, prefers email over calls.
```

Readers treat it as follows:

- title `Client contacts`
- scope `projects/acme-site`
- no id
- `type: note`, `status: active`, no tags
- `created` and `updated` from the file's mtime.

It can be found by search and fetched with `get("Client contacts")`. The file is not modified unless it is superseded or forgotten.

### 12.4 User properties are preserved

If a user adds `rating: 5` and `aliases: [style]` in Obsidian, a later rewrite by a writer keeps both. They come after the known keys, in the order the user wrote them, and in canonical form:

```yaml
updated: 2026-10-05T12:00:00Z
rating: 5
aliases:
  - style
```

## 13. Known limitations of version 0.1

- **Slugs for non-Latin text** all become `memory`, `memory-2`, and so on (§4.1). Users can rename the files.
- **Concurrent supersedes.** If two writers supersede the same memory at the same moment, both new memories stay `active`, and the old one's `superseded_by` names whichever writer finished last. Nothing is lost, and the user can supersede or forget one of them.
- **Uniqueness across folders** depends on the writer knowing about every existing file (§4.2). lil memory checks the index shared by all its processes, which is up to 2 seconds behind files created by hand. Two writers creating the same slug in *different* folders at the same moment can also produce a duplicate stem. Within one folder, the no-clobber rule in §9 always prevents it. Readers already handle duplicates (§8).
