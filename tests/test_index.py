import os
import shutil
import sqlite3
import time
from pathlib import Path

import pytest

from lil_memory import index as index_mod
from lil_memory import vault
from lil_memory.index import Index, fts_query, rank


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def touch_later(path: Path) -> None:
    """Bump mtime so a same-size rewrite within one clock tick is still noticed."""
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


@pytest.fixture
def idx(tmp_path):
    index = Index(tmp_path)
    yield index
    index.close()


def paths(rows):
    return [r["path"] for r in rows]


def test_fts5_is_available():
    index_mod.check_fts5()


def test_index_lives_in_the_data_folder(tmp_path, idx):
    assert (tmp_path / ".lil-memory" / "index.sqlite").exists()


def test_refresh_picks_up_new_changed_moved_and_deleted_files(tmp_path, idx):
    write(tmp_path, "global/a.md", "---\ntype: fact\n---\nalpha bravo")
    write(tmp_path, "global/b.md", "charlie")
    assert idx.refresh(force=True) == 2
    assert paths(idx.search("alpha")) == ["global/a.md"]

    write(tmp_path, "global/a.md", "---\ntype: fact\n---\ndelta bravo")  # edited in Obsidian
    touch_later(tmp_path / "global/a.md")
    os.rename(tmp_path / "global/b.md", tmp_path / "projects-b.md")  # moved to root: not a memory
    (tmp_path / "projects").mkdir()
    write(tmp_path, "projects/c.md", "echo")
    assert idx.refresh(force=True) == 4
    assert idx.search("alpha") == []
    assert paths(idx.search("delta")) == ["global/a.md"]
    assert idx.search("charlie") == []
    assert paths(idx.search("echo")) == ["projects/c.md"]

    (tmp_path / "projects/c.md").unlink()
    assert idx.refresh(force=True) == 1
    assert idx.search("echo") == []
    assert idx.refresh(force=True) == 0


def test_refresh_is_throttled(tmp_path, idx, monkeypatch):
    idx.refresh()
    write(tmp_path, "global/a.md", "alpha")
    assert idx.refresh() == 0  # within the 2 s window
    monkeypatch.setattr(index_mod, "REFRESH_INTERVAL", 0.0)
    assert idx.refresh() == 1


def test_note_indexes_immediately(tmp_path, idx):
    idx.refresh()
    memory = vault.create(tmp_path, "Fresh memory", "fact")
    idx.note(memory.path)
    assert paths(idx.search("fresh")) == [memory.path]
    trashed = vault.forget(tmp_path, memory.path)
    idx.note(memory.path, trashed)
    assert idx.search("fresh") == []
    assert idx.stats()["memories"] == 0


def test_search_filters(tmp_path, idx):
    vault.create(tmp_path, "Coffee black no sugar", "preference", "global", ["food"])
    vault.create(tmp_path, "Coffee machine is broken", "fact", "projects/office", ["office"])
    vault.create(
        tmp_path, "Coffee supplier is Bean Co", "fact", "projects/office/kitchen", ["food/supply"]
    )
    vault.create(tmp_path, "Coffee budget", "decision", "projects-old", [])
    idx.refresh(force=True)
    assert len(idx.search("coffee")) == 4
    assert sorted(paths(idx.search("coffee", scope="projects"))) == [
        "projects/office/coffee-machine-is-broken.md",
        "projects/office/kitchen/coffee-supplier-is-bean-co.md",
    ]
    assert paths(idx.search("coffee", type="preference")) == ["global/coffee-black-no-sugar.md"]
    assert sorted(paths(idx.search("coffee", tags=["#food"]))) == [  # nested tags match
        "global/coffee-black-no-sugar.md",
        "projects/office/kitchen/coffee-supplier-is-bean-co.md",
    ]
    assert paths(idx.search("coffee", tags=["FOOD", "food/supply"])) == [
        "projects/office/kitchen/coffee-supplier-is-bean-co.md"
    ]
    assert len(idx.search("coffee", limit=2)) == 2


def test_search_excludes_inactive_and_non_memories(tmp_path, idx):
    old = vault.create(tmp_path, "Deploys to Netlify", "decision")
    vault.supersede(tmp_path, old, "Deploys to Cloudflare")
    write(tmp_path, "inbox/pending.md", "Deploys to Fly")
    write(tmp_path, "imports/chatgpt/chat.md", "Deploys to Heroku")
    write(tmp_path, "README.md", "Deploys to Render")
    idx.refresh(force=True)
    assert paths(idx.search("deploys")) == ["global/deploys-to-cloudflare.md"]


def test_search_matches_prefixes_titles_and_diacritics(tmp_path, idx):
    write(tmp_path, "global/british-spelling.md", "Use colour, not color.")
    write(tmp_path, "global/cafe.md", "Meet at the café.")
    idx.refresh(force=True)
    assert paths(idx.search("spell")) == ["global/british-spelling.md"]
    assert paths(idx.search("cafe")) == ["global/cafe.md"]


def test_search_survives_hostile_queries(tmp_path, idx):
    write(tmp_path, "global/a.md", "alpha")
    idx.refresh(force=True)
    for query in ['"', "NOT", "a* OR (", "alpha) OR body:x", "^^^", "' ; DROP TABLE files; --"]:
        idx.search(query)
    assert paths(idx.search('"alpha"')) == ["global/a.md"]


def test_empty_query_lists_most_recent(tmp_path, idx):
    for i, name in enumerate(["old", "mid", "new"]):
        write(tmp_path, f"global/{name}.md", name)
        os.utime(tmp_path / f"global/{name}.md", (1_000_000 + i, 1_000_000 + i))
    idx.refresh(force=True)
    assert paths(idx.search("", limit=2)) == ["global/new.md", "global/mid.md"]


def test_rank_prefers_relevance_with_a_mild_recency_boost():
    assert rank(-10.0, 0) > rank(-10.0, 365)
    assert rank(-10.0, 3650) > rank(-7.0, 0)  # relevance beats recency
    assert rank(-10.0, 0) == pytest.approx(12.5)
    assert rank(-10.0, 90) == pytest.approx(11.25)
    assert rank(-10.0, -5) == rank(-10.0, 0)  # future timestamps get no extra boost


def test_fts_query_quotes_words():
    assert fts_query("British spelling, a?") == '"british"* OR "spelling"*'
    assert fts_query("go to it") == '"go" OR "to" OR "it"'
    assert fts_query("!!! ?") is None


def test_resolve_by_id_stem_path_and_wikilink(tmp_path, idx):
    m = vault.create(tmp_path, "Prefers tea", "preference", "projects/x")
    write(tmp_path, "global/Prefers-Tea.md", "dup stem by hand")
    idx.refresh(force=True)
    assert paths(idx.resolve(m.id)) == [m.path]
    assert paths(idx.resolve(m.id.lower())) == [m.path]
    assert paths(idx.resolve("projects/x/prefers-tea")) == [m.path]
    assert paths(idx.resolve("projects/X/Prefers-Tea.md")) == [m.path]
    assert paths(idx.resolve("[[projects/x/prefers-tea|alias]]")) == [m.path]
    assert paths(idx.resolve("prefers-tea")) == ["global/Prefers-Tea.md", m.path]  # ambiguous
    assert idx.resolve("nothing") == []
    assert idx.resolve("01J9XK3M7Q2R8S5T6V7W8X9Y0Z") == []


def test_duplicate_ids_belong_to_the_first_path(tmp_path, idx):
    m = vault.create(tmp_path, "Original", "fact", "projects/b")
    (tmp_path / "global").mkdir()
    shutil.copy(tmp_path / m.path, tmp_path / "global" / "copy.md")
    idx.refresh(force=True)
    assert paths(idx.resolve(m.id)) == ["global/copy.md"]
    ids = {r["path"]: r["id"] for r in idx.search("original")}
    assert ids == {"global/copy.md": m.id, m.path: None}


def test_stem_taken_covers_imports_and_root(tmp_path, idx):
    write(tmp_path, "imports/claude/chat.md", "x")
    write(tmp_path, "Readme.md", "x")
    write(tmp_path, ".obsidian/hidden.md", "x")
    idx.refresh(force=True)
    assert idx.stem_taken("CHAT") and idx.stem_taken("readme")
    assert not idx.stem_taken("hidden")


def test_scopes_and_stats(tmp_path, idx):
    vault.create(tmp_path, "One", "fact")
    vault.create(tmp_path, "Two", "fact")
    old = vault.create(tmp_path, "Three", "fact", "projects/x")
    vault.supersede(tmp_path, old, "Four")
    write(tmp_path, "global/broken.md", "---\n[unclosed\n---\nBody")
    idx.refresh(force=True)
    assert idx.scopes() == [("global", 3), ("projects/x", 1)]
    assert idx.stats() == {"memories": 5, "active": 4, "malformed": 1}


def test_deleting_the_index_loses_nothing(tmp_path):
    first = Index(tmp_path)
    vault.create(tmp_path, "Remember the milk", "note")
    first.refresh(force=True)
    first.close()
    for name in os.listdir(tmp_path / ".lil-memory"):
        os.unlink(tmp_path / ".lil-memory" / name)
    second = Index(tmp_path)
    second.refresh(force=True)
    assert paths(second.search("milk")) == ["global/remember-the-milk.md"]
    second.close()


def test_reindex_rebuilds_everything(tmp_path, idx):
    vault.create(tmp_path, "Alpha", "fact")
    idx.refresh(force=True)
    idx.db.execute("DELETE FROM fts")
    assert idx.search("alpha") == []
    assert idx.reindex() == 1
    assert len(idx.search("alpha")) == 1


def test_schema_version_mismatch_rebuilds(tmp_path):
    db = tmp_path / ".lil-memory" / "index.sqlite"
    db.parent.mkdir()
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE files (junk)")
    conn.execute("PRAGMA user_version = 99")
    conn.commit()
    conn.close()
    vault.create(tmp_path, "Alpha", "fact")
    idx = Index(tmp_path)
    idx.refresh(force=True)
    assert len(idx.search("alpha")) == 1
    idx.close()


def test_hand_edit_is_visible_within_two_seconds(tmp_path, idx, monkeypatch):
    m = vault.create(tmp_path, "Favourite colour is green", "preference")
    idx.refresh(force=True)
    time.sleep(0.01)
    path = tmp_path / m.path
    path.write_text(path.read_text().replace("green", "purple"))
    touch_later(path)
    monkeypatch.setattr(index_mod, "REFRESH_INTERVAL", 2.0)
    idx._last_scan = time.monotonic() - 2.0  # the throttle window has just expired
    assert idx.refresh() == 1
    assert paths(idx.search("purple")) == [m.path]


def test_resolve_paths_case_insensitively_beyond_ascii(tmp_path, idx):
    write(tmp_path, "Ärende/Möte.md", "Fika at three")
    idx.refresh(force=True)
    assert paths(idx.resolve("ärende/möte")) == ["Ärende/Möte.md"]
    assert paths(idx.resolve("MÖTE")) == ["Ärende/Möte.md"]
