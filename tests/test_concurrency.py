"""Several server processes share one vault, because every AI client starts its own.

These tests run real processes against one vault at the same moment and check that no
memory is lost or overwritten, and that the shared SQLite index stays usable.
"""

import asyncio
import multiprocessing
import time
from pathlib import Path

import pytest

from lil_memory import vault
from lil_memory.index import Index
from lil_memory.server import build

PROCESSES = 8
PER_PROCESS = 25


def wait_until(start_at: float) -> None:
    time.sleep(max(0.0, start_at - time.time()))


def call(mcp, tool: str, **args) -> str:
    result = asyncio.run(mcp.call_tool(tool, args))
    blocks = result[0] if isinstance(result, tuple) else result
    return blocks[0].text


def remember_many(root: str, worker: int, start_at: float) -> list[str]:
    mcp = build(Path(root))
    wait_until(start_at)
    errors = []
    for i in range(PER_PROCESS):
        try:
            # Same content everywhere, so every process races for the same slugs.
            call(
                mcp,
                "remember",
                content="Shared slug for everyone",
                type="fact",
                tags=[f"w{worker}"],
            )
            if i % 5 == 0:
                call(mcp, "recall", query="shared slug")
        except Exception as e:  # noqa: BLE001 - reported back to the test
            errors.append(repr(e))
    return errors


def refresh_loop(root: str, start_at: float) -> list[str]:
    index = Index(Path(root))
    wait_until(start_at)
    errors = []
    deadline = time.time() + 2.0
    while time.time() < deadline:
        try:
            index.refresh(force=True)
            index.search("shared")
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))
    return errors


def open_index(root: str, start_at: float) -> str:
    wait_until(start_at)
    index = Index(Path(root))
    index.refresh(force=True)
    return str(index.stats())


def update_same(root: str, worker: int, start_at: float) -> str:
    mcp = build(Path(root))
    wait_until(start_at)
    try:
        return call(mcp, "update", ref="contested", content=f"Version from worker {worker}")
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"


def run(func, args_list):
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(len(args_list)) as pool:
        return pool.starmap(func, args_list, chunksize=1)


@pytest.fixture
def root(tmp_path):
    (tmp_path / ".lil-memory").mkdir()
    return tmp_path


def test_concurrent_remembers_lose_nothing(root):
    start_at = time.time() + 2.0
    jobs = [(str(root), w, start_at) for w in range(PROCESSES)]
    errors = run(remember_many, jobs)
    assert errors == [[]] * PROCESSES

    paths = sorted(p.relative_to(root).as_posix() for p in (root / "global").iterdir())
    total = PROCESSES * PER_PROCESS
    assert len(paths) == total
    assert not [p for p in paths if p.split("/")[-1].startswith(".")]  # no temp files left
    expected = ["global/shared-slug-for-everyone.md"] + [
        f"global/shared-slug-for-everyone-{n}.md" for n in range(2, total + 1)
    ]
    assert sorted(paths) == sorted(expected)

    memories = [vault.read(root, p) for p in paths]
    assert all(not m.malformed and m.id for m in memories)
    assert len({m.id for m in memories}) == total
    tags = [m.tags[0] for m in memories]
    assert all(tags.count(f"w{w}") == PER_PROCESS for w in range(PROCESSES))

    index = Index(root)
    index.refresh(force=True)
    assert index.stats() == {"memories": total, "active": total, "malformed": 0}
    assert len(index.search("shared slug", limit=total)) == total


def test_concurrent_refreshes_and_writes_share_the_index(root):
    for i in range(50):
        vault.create(root, f"Shared seed {i}", "note")
    start_at = time.time() + 2.0
    refreshers = [(str(root), start_at) for _ in range(4)]
    writers = [(str(root), w, start_at) for w in range(4)]
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(8) as pool:
        refreshed = pool.starmap_async(refresh_loop, refreshers, chunksize=1)
        written = pool.starmap_async(remember_many, writers, chunksize=1)
        assert refreshed.get(timeout=120) == [[]] * 4
        assert written.get(timeout=120) == [[]] * 4
    index = Index(root)
    index.refresh(force=True)
    assert index.stats()["memories"] == 50 + 4 * PER_PROCESS


def test_many_processes_create_the_index_at_once(tmp_path):
    vault.create(tmp_path, "Exists before any index", "fact")
    start_at = time.time() + 2.0
    results = run(open_index, [(str(tmp_path), start_at) for _ in range(PROCESSES)])
    assert results == [str({"memories": 1, "active": 1, "malformed": 0})] * PROCESSES


def test_concurrent_updates_of_one_memory_keep_every_version(root):
    vault.create(root, "contested", "decision")
    Index(root).refresh(force=True)
    start_at = time.time() + 2.0
    results = run(update_same, [(str(root), w, start_at) for w in range(4)])
    succeeded = [r for r in results if not r.startswith("error")]
    assert succeeded, results
    for r in results:  # losers may see "already superseded", never anything else
        assert not r.startswith("error") or "already superseded" in r, r

    old = vault.read(root, "global/contested.md")
    assert old.status == "superseded"
    versions = [m for m in map(lambda p: vault.read(root, p), _paths(root)) if m.path != old.path]
    assert len(versions) == len(succeeded)
    assert all(m.meta["supersedes"] == "[[contested]]" for m in versions)
    assert old.meta["superseded_by"] in {f"[[{m.stem}]]" for m in versions}


def _paths(root: Path) -> list[str]:
    return [p for p, _, memory in vault.scan(root) if memory]
