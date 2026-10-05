"""SQLite + FTS5 cache over the vault: incremental refresh, search and ranking.

The index is disposable: deleting .lil-memory/index.sqlite never loses data.
Knows nothing about MCP.
"""

import os
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from lil_memory import vault
from lil_memory.frontmatter import format_time
from lil_memory.ids import is_id

SCHEMA_VERSION = 1
REFRESH_INTERVAL = 2.0  # seconds between mtime scans before read operations
RECENCY_WEIGHT = 0.25  # a brand-new memory scores up to 25% higher than an old one
RECENCY_HALF_LIFE_DAYS = 90.0

_SCHEMA = """
CREATE TABLE files (
    rowid INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    stem TEXT NOT NULL,           -- lowercased, for uniqueness and lookups
    mtime_ns INTEGER NOT NULL,
    size INTEGER NOT NULL,
    is_memory INTEGER NOT NULL,
    id TEXT,
    type TEXT,
    status TEXT,
    scope TEXT,
    tags TEXT,                    -- " tag1 tag2 ", for LIKE filters
    source TEXT,
    created TEXT,
    recency REAL,                 -- epoch seconds, max(updated, mtime)
    malformed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX files_stem ON files(stem);
CREATE INDEX files_id ON files(id);
CREATE INDEX files_scope ON files(scope);
CREATE VIRTUAL TABLE fts USING fts5(
    title, body, tags, tokenize = 'unicode61 remove_diacritics 2'
);
"""

# The id belongs to the copy whose path sorts first (SPEC §7).
_COLUMNS = """
    f.path, f.type, f.status, f.scope, trim(f.tags) AS tags, f.source, f.created, f.recency,
    fts.body AS body,
    CASE WHEN f.path = (SELECT min(g.path) FROM files g WHERE g.id = f.id) THEN f.id END AS id
"""


def check_fts5() -> None:
    try:
        sqlite3.connect(":memory:").execute("CREATE VIRTUAL TABLE t USING fts5(x)")
    except sqlite3.OperationalError as e:
        raise RuntimeError(
            f"this Python's SQLite ({sqlite3.sqlite_version}) lacks FTS5; "
            "install a Python build with FTS5, e.g. via `uv python install`"
        ) from e


def rank(bm25: float, age_days: float) -> float:
    """Higher is better. SQLite's bm25() is negative, more negative meaning a better match."""
    boost = RECENCY_WEIGHT * 0.5 ** (max(age_days, 0.0) / RECENCY_HALF_LIFE_DAYS)
    return -bm25 * (1.0 + boost)


def fts_query(text: str) -> str | None:
    """Turn free text into a safe FTS5 query: any word may match, longer words as prefixes."""
    words = list(dict.fromkeys(re.findall(r"\w{2,}", text.lower())))[:32]
    if not words:
        return None
    return " OR ".join(f'"{w}"*' if len(w) >= 3 else f'"{w}"' for w in words)


class Index:
    def __init__(self, root: Path):
        self.root = root
        (root / vault.DATA_DIR).mkdir(exist_ok=True)
        self.db = sqlite3.connect(
            root / vault.DATA_DIR / "index.sqlite",
            timeout=30,
            isolation_level=None,
            check_same_thread=False,
        )
        self.db.row_factory = sqlite3.Row
        self._use_wal()
        self.db.execute("PRAGMA synchronous = NORMAL")
        self._last_scan = float("-inf")
        with self._write():
            if self.db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                # One statement at a time: executescript() would commit our transaction.
                for statement in ["DROP TABLE IF EXISTS files", "DROP TABLE IF EXISTS fts"]:
                    self.db.execute(statement)
                for statement in filter(str.strip, _SCHEMA.split(";")):
                    self.db.execute(statement)
                self.db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _use_wal(self) -> None:
        """WAL lets several server processes share the index. The mode is stored in the file,
        but switching to it ignores the busy timeout, so a fresh index opened by several
        processes at once needs a few retries."""
        for _ in range(100):
            try:
                if self.db.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                    self.db.execute("PRAGMA journal_mode = WAL")
                return
            except sqlite3.OperationalError:
                time.sleep(0.05)
        raise RuntimeError("could not open the index in WAL mode; is another process stuck?")

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def _write(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")

    # Keeping the index in step with the files

    def refresh(self, force: bool = False) -> int:
        """Re-index files added, changed or removed since the last scan. Returns the count."""
        if not force and time.monotonic() - self._last_scan < REFRESH_INTERVAL:
            return 0
        seen = {p: (st.st_mtime_ns, st.st_size, mem) for p, st, mem in vault.scan(self.root)}
        known = {
            r[0]: (r[1], r[2]) for r in self.db.execute("SELECT path, mtime_ns, size FROM files")
        }
        changed = [p for p, (m, s, _) in seen.items() if known.get(p) != (m, s)]
        removed = [p for p in known if p not in seen]
        if changed or removed:
            with self._write():
                for path in removed:
                    self._delete(path)
                for path in changed:
                    self._upsert(path, *seen[path])
        self._last_scan = time.monotonic()
        return len(changed) + len(removed)

    def reindex(self) -> int:
        with self._write():
            self.db.execute("DELETE FROM files")
            self.db.execute("DELETE FROM fts")
        return self.refresh(force=True)

    def note(self, *paths: str) -> None:
        """Index files this process just wrote, moved or removed, without waiting for a scan."""
        with self._write():
            for path in paths:
                if (memory := vault.kind(path)) is None:
                    self._delete(path)
                    continue
                try:
                    st = os.stat(self.root / path)
                except FileNotFoundError:
                    self._delete(path)
                    continue
                self._upsert(path, st.st_mtime_ns, st.st_size, memory)

    def _delete(self, path: str) -> None:
        row = self.db.execute("SELECT rowid FROM files WHERE path = ?", (path,)).fetchone()
        if row:
            self.db.execute("DELETE FROM fts WHERE rowid = ?", (row[0],))
            self.db.execute("DELETE FROM files WHERE rowid = ?", (row[0],))

    def _upsert(self, path: str, mtime_ns: int, size: int, is_memory: bool) -> None:
        self._delete(path)
        stem = PurePosixPath(path).stem
        if not is_memory:  # tracked only so new slugs stay unique (SPEC §4.2)
            self.db.execute(
                "INSERT INTO files (path, stem, mtime_ns, size, is_memory) VALUES (?, ?, ?, ?, 0)",
                (path, stem.lower(), mtime_ns, size),
            )
            return
        try:
            m = vault.read(self.root, path)
        except OSError:
            return
        tags = " ".join(m.tags)
        rowid = self.db.execute(
            "INSERT INTO files (path, stem, mtime_ns, size, is_memory, id, type, status, scope,"
            " tags, source, created, recency, malformed)"
            " VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                path,
                stem.lower(),
                mtime_ns,
                size,
                m.id,
                m.type,
                m.status,
                m.scope,
                f" {tags} ",
                m.source,
                format_time(m.created),
                m.recency.timestamp(),
                int(m.malformed),
            ),
        ).lastrowid
        self.db.execute(
            "INSERT INTO fts (rowid, title, body, tags) VALUES (?, ?, ?, ?)",
            (rowid, stem.replace("-", " "), m.body, tags),
        )

    # Queries

    def search(
        self,
        query: str = "",
        scope: str | None = None,
        type: str | None = None,
        tags: list[str] | None = None,
        limit: int = 10,
        exclude: str | None = None,
    ) -> list[dict]:
        """Active memories matching the query and filters, best first.

        With no searchable words, returns the most recent matching memories instead.
        """
        where, args = ["f.status = 'active'"], []
        if scope:
            where.append("(f.scope = ? OR f.scope LIKE ? ESCAPE '\\')")
            args += [scope, _escape_like(scope) + "/%"]
        if type:
            where.append("f.type = ?")
            args.append(type)
        for tag in tags or []:
            tag = tag.strip().lstrip("#")
            where.append("(f.tags LIKE ? ESCAPE '\\' OR f.tags LIKE ? ESCAPE '\\')")
            args += [f"% {_escape_like(tag)} %", f"% {_escape_like(tag)}/%"]
        if exclude:
            where.append("f.path != ?")
            args.append(exclude)
        match = fts_query(query)
        if match is None:
            sql = f"SELECT {_COLUMNS} FROM files f JOIN fts ON fts.rowid = f.rowid WHERE "
            sql += " AND ".join(where) + " ORDER BY f.recency DESC LIMIT ?"
            return [dict(r) for r in self.db.execute(sql, [*args, limit])]
        sql = f"SELECT {_COLUMNS}, bm25(fts, 5.0, 1.0, 3.0) AS score FROM fts"
        sql += " JOIN files f ON f.rowid = fts.rowid WHERE fts MATCH ? AND "
        sql += " AND ".join(where) + " ORDER BY score LIMIT ?"
        now = time.time()
        scored = []
        for row in map(dict, self.db.execute(sql, [match, *args, max(limit * 4, 40)])):
            scored.append((rank(row.pop("score"), (now - row["recency"]) / 86400), row))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [row for _, row in scored[:limit]]

    def resolve(self, ref: str) -> list[dict]:
        """Memories a free-form reference points to (SPEC §8.1). Several means ambiguous."""
        ref = ref.strip()
        if is_id(ref):
            sql = f"SELECT {_COLUMNS} FROM files f JOIN fts ON fts.rowid = f.rowid"
            rows = self.db.execute(sql + " WHERE f.id = ? ORDER BY f.path LIMIT 1", (ref.upper(),))
            if found := [dict(r) for r in rows]:
                return found
        target = ref.removeprefix("[[").removesuffix("]]")
        target = re.split(r"[|#]", target, maxsplit=1)[0].strip()
        if target.lower().endswith(".md"):
            target = target[:-3]
        if "/" in target:
            cond, arg = "lower(f.path) = ?", target.lower() + ".md"
        else:
            cond, arg = "f.stem = ?", target.lower()
        sql = f"SELECT {_COLUMNS} FROM files f JOIN fts ON fts.rowid = f.rowid WHERE {cond}"
        return [dict(r) for r in self.db.execute(sql + " ORDER BY f.path", (arg,))]

    def stem_taken(self, stem: str) -> bool:
        return (
            self.db.execute("SELECT 1 FROM files WHERE stem = ?", (stem.lower(),)).fetchone()
            is not None
        )

    def scopes(self) -> list[tuple[str, int]]:
        sql = "SELECT scope, count(*) FROM files WHERE is_memory AND status = 'active'"
        return [tuple(r) for r in self.db.execute(sql + " GROUP BY scope ORDER BY scope")]

    def stats(self) -> dict[str, int]:
        sql = "SELECT count(*), coalesce(sum(status = 'active'), 0), coalesce(sum(malformed), 0)"
        total, active, malformed = self.db.execute(sql + " FROM files WHERE is_memory").fetchone()
        return {"memories": total, "active": active, "malformed": malformed}


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
