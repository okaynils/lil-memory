"""Memory files on disk: fields, slugs, atomic writes and the operations on them.

Implements docs/SPEC.md. Knows nothing about the index or MCP.
"""

import os
import re
import tempfile
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import count
from pathlib import Path, PurePosixPath

from lil_memory.frontmatter import dump, format_time, parse, parse_time
from lil_memory.ids import is_id, new_id

TYPES = ("fact", "preference", "project", "decision", "note")
STATUSES = ("active", "pending", "superseded")
DATA_DIR = ".lil-memory"
FORMAT = "0.1"


class VaultError(ValueError):
    """A request the vault cannot carry out; the message is meant for the user."""


@dataclass
class Memory:
    path: str  # relative to the vault root, with "/" separators
    meta: dict
    body: str
    mtime: float
    malformed: bool = False

    @property
    def stem(self) -> str:
        return PurePosixPath(self.path).stem

    @property
    def scope(self) -> str:
        return str(PurePosixPath(self.path).parent)

    @property
    def id(self) -> str | None:
        value = self.meta.get("id")
        return value.upper() if is_id(value) else None

    @property
    def type(self) -> str:
        value = str(self.meta.get("type", "")).lower()
        return value if value in TYPES else "note"

    @property
    def status(self) -> str:
        value = str(self.meta.get("status", "")).lower()
        if value in STATUSES:
            return value
        return "pending" if self.path.startswith("inbox/") else "active"

    @property
    def tags(self) -> list[str]:
        value = self.meta.get("tags", [])
        items = [value] if isinstance(value, str) else value if isinstance(value, list) else []
        return [t.strip().lstrip("#") for t in items if isinstance(t, str) and t.strip("# ")]

    @property
    def source(self) -> str:
        value = self.meta.get("source", "")
        return value if isinstance(value, str) else ""

    @property
    def created(self) -> datetime:
        return parse_time(self.meta.get("created")) or datetime.fromtimestamp(self.mtime, UTC)

    @property
    def updated(self) -> datetime:
        return parse_time(self.meta.get("updated")) or self.created

    @property
    def recency(self) -> datetime:
        """Hand edits don't touch `updated`, so the file's mtime counts too (SPEC §6.3)."""
        return max(self.updated, datetime.fromtimestamp(self.mtime, UTC))

    def link(self, field: str) -> str | None:
        value = self.meta.get(field)
        return value if isinstance(value, str) and value else None


# Paths and names


def kind(path: str) -> bool | None:
    """True for a memory file, False for another visible .md file, None if readers skip it."""
    parts = path.split("/")
    if any(p.startswith(".") for p in parts) or not path.lower().endswith(".md"):
        return None
    return len(parts) > 1 and parts[0] != "imports"


def scan(root: Path) -> Iterator[tuple[str, os.stat_result, bool]]:
    """Yield (path, stat, is_memory) for every visible .md file in the vault (SPEC §2.1)."""
    stack = [("", str(root))]
    while stack:
        prefix, directory = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith("."):
                continue
            rel = prefix + entry.name
            if entry.is_dir(follow_symlinks=False):
                stack.append((rel + "/", entry.path))
            elif (memory := kind(rel)) is not None and entry.is_file():
                yield rel, entry.stat(), memory


def slugify(text: str) -> str:
    """Make a filename stem from content (SPEC §4.1), before uniqueness."""
    line = next((ln for ln in text.splitlines() if any(c.isalnum() for c in ln)), "")
    decomposed = unicodedata.normalize("NFKD", line)
    ascii_ish = "".join(c for c in decomposed if unicodedata.category(c) != "Mn").lower()
    slug = "-".join(re.sub(r"[^a-z0-9]+", "-", ascii_ish).strip("-").split("-")[:6])
    if len(slug) > 60:
        cut = slug.rfind("-", 0, 61)
        slug = (slug[:cut] if cut > 0 else slug[:60]).rstrip("-")
    return slug or "memory"


def validate_scope(scope: str) -> str:
    parts = scope.split("/")
    if (
        not scope
        or scope.startswith("/")
        or "\\" in scope
        or any(p in ("", ".", "..") or p.startswith(".") for p in parts)
        or parts[0] == "imports"
    ):
        raise VaultError(f"invalid scope {scope!r}: use a relative folder path like 'global'")
    return scope


def validate_tags(tags: list[str]) -> list[str]:
    clean = [t.strip().lstrip("#") for t in tags]
    for tag in clean:
        if not tag or any(c.isspace() for c in tag):
            raise VaultError(f"invalid tag {tag!r}: tags cannot be empty or contain spaces")
    return clean


# Reading and writing


def read(root: Path, path: str) -> Memory:
    with open(root / path, "rb") as f:
        raw = f.read()
        mtime = os.fstat(f.fileno()).st_mtime
    meta, body, malformed = parse(raw.decode("utf-8", errors="replace"))
    return Memory(path, meta or {}, body.strip(), mtime, malformed)


def _temp_file(directory: Path, name: str, text: str) -> str:
    fd, tmp = tempfile.mkstemp(prefix=f".{name}.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        os.unlink(tmp)
        raise
    return tmp


def write_new(root: Path, scope: str, base: str, text: str, taken: Callable[[str], bool]) -> str:
    """Atomically create <scope>/<unique stem>.md without overwriting anything (SPEC §9)."""
    directory = root / scope
    directory.mkdir(parents=True, exist_ok=True)
    tmp = _temp_file(directory, base, text)
    os.chmod(tmp, 0o644)
    try:
        for n in count(1):
            stem = base if n == 1 else f"{base}-{n}"
            if taken(stem):
                continue
            target = directory / f"{stem}.md"
            try:
                os.link(tmp, target)
            except FileExistsError:
                continue
            return f"{scope}/{stem}.md"
    finally:
        os.unlink(tmp)
    raise AssertionError("unreachable")


def rewrite(root: Path, memory: Memory) -> None:
    target = root / memory.path
    tmp = _temp_file(target.parent, target.name, dump(memory.meta, memory.body))
    try:
        os.chmod(tmp, os.stat(target).st_mode & 0o777)
        os.replace(tmp, target)
    except BaseException:
        os.unlink(tmp)
        raise


# Operations (SPEC §10)


def disk_stems(root: Path) -> Callable[[str], bool]:
    """A `taken` check that walks the vault. The server uses the index instead."""
    stems = {PurePosixPath(path).stem.lower() for path, _, _ in scan(root)}
    return lambda stem: stem.lower() in stems


def create(
    root: Path,
    content: str,
    type: str,
    scope: str = "global",
    tags: list[str] | None = None,
    source: str = "",
    supersedes: Memory | None = None,
    taken: Callable[[str], bool] | None = None,
) -> Memory:
    """Write a new active memory; if it supersedes another, mark that one too."""
    if not content.strip():
        raise VaultError("content is empty")
    if type not in TYPES:
        raise VaultError(f"invalid type {type!r}: use one of {', '.join(TYPES)}")
    if supersedes is not None:
        _check_supersedable(supersedes)
    now = format_time(datetime.now(UTC))
    meta = {
        "id": new_id(),
        "type": type,
        "status": "active",
        "tags": validate_tags(tags or []),
        "source": source,
        "created": now,
        "updated": now,
        "supersedes": f"[[{supersedes.stem}]]" if supersedes else "",
    }
    text = dump(meta, content)
    path = write_new(root, validate_scope(scope), slugify(content), text, taken or disk_stems(root))
    new = read(root, path)
    if supersedes is not None:
        try:
            mark_superseded(root, supersedes.path, new.stem)
        except BaseException:  # e.g. another writer superseded it first: undo our half
            os.unlink(root / path)
            raise
    return new


def supersede(
    root: Path, old: Memory, content: str, source: str = "", taken=None
) -> tuple[Memory, Memory]:
    """Replace `old` with new content in the same scope; returns (new, old)."""
    new = create(root, content, old.type, old.scope, old.tags, source, old, taken)
    return new, read(root, old.path)


def mark_superseded(root: Path, path: str, new_stem: str) -> None:
    fresh = read(root, path)  # re-read so a just-saved hand edit is not lost (SPEC §9)
    _check_supersedable(fresh)
    fresh.meta.update(
        status="superseded",
        superseded_by=f"[[{new_stem}]]",
        updated=format_time(datetime.now(UTC)),
    )
    if fresh.id is None:
        fresh.meta["id"] = new_id()
    rewrite(root, fresh)


def _check_supersedable(memory: Memory) -> None:
    if memory.malformed:
        raise VaultError(f"{memory.path} has malformed frontmatter; fix it by hand first")
    if memory.status == "superseded":
        newer = memory.link("superseded_by") or "a newer memory"
        raise VaultError(f"{memory.stem} is already superseded by {newer}")


def forget(root: Path, path: str) -> str:
    """Move a memory to .lil-memory/trash/, keeping its relative path (SPEC §10.3)."""
    trash = PurePosixPath(DATA_DIR, "trash", path)
    if (root / trash).exists():
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        trash = trash.with_name(f"{trash.stem}-{stamp}{trash.suffix}")
    (root / trash).parent.mkdir(parents=True, exist_ok=True)
    os.rename(root / path, root / trash)
    return str(trash)
