import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lil_memory import vault
from lil_memory.ids import is_id
from lil_memory.vault import VaultError


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode())
    return path


# Parsing


def test_parse_reads_every_scalar_as_string():
    meta, body, malformed = vault.parse(
        "---\nid: 01J9XK3M7Q2R8S5T6V7W8X9Y0Z\ncreated: 2026-10-05T09:12:00Z\n"
        "rating: 5\nflag: yes\nempty:\n---\nHello\n"
    )
    assert not malformed
    assert meta == {
        "id": "01J9XK3M7Q2R8S5T6V7W8X9Y0Z",
        "created": "2026-10-05T09:12:00Z",
        "rating": "5",
        "flag": "yes",
        "empty": "",
    }
    assert body == "Hello\n"


def test_parse_accepts_bom_crlf_and_trailing_spaces_on_delimiters():
    meta, body, _ = vault.parse("﻿--- \r\ntype: fact\r\n---\t\r\nBody\r\n")
    assert meta == {"type": "fact"}
    assert body == "Body\n"


def test_parse_without_frontmatter_or_closing_delimiter():
    assert vault.parse("Just text\n") == (None, "Just text\n", False)
    assert vault.parse("---\ntype: fact\nno closing\n") == (
        None,
        "---\ntype: fact\nno closing\n",
        False,
    )


def test_parse_empty_frontmatter_is_empty_mapping():
    assert vault.parse("---\n---\nBody") == ({}, "Body", False)
    assert vault.parse("---\n  \n---\nBody") == ({}, "Body", False)


@pytest.mark.parametrize("raw", ["type: [unclosed", "- a\n- b", "just a string"])
def test_parse_malformed_frontmatter_keeps_body(raw):
    meta, body, malformed = vault.parse(f"---\n{raw}\n---\nBody\n")
    assert meta is None and malformed and body == "Body\n"


# Serialization


def test_dump_canonical_order_block_lists_and_quoted_wikilinks():
    meta = {
        "aliases": ["style"],
        "superseded_by": "[[b]]",
        "tags": ["writing", "tone"],
        "id": "01J9XK3M7Q2R8S5T6V7W8X9Y0Z",
        "type": "preference",
        "status": "active",
        "source": "claude-desktop / claude-opus-5.5",
        "created": "2026-10-05T09:12:00Z",
        "updated": "2026-10-05T09:12:00Z",
        "supersedes": "[[a]]",
        "rating": "5",
    }
    assert vault.dump(meta, "\n\nBody text.\n\n") == (
        "---\n"
        "id: 01J9XK3M7Q2R8S5T6V7W8X9Y0Z\n"
        "type: preference\n"
        "status: active\n"
        "tags:\n"
        "  - writing\n"
        "  - tone\n"
        "source: claude-desktop / claude-opus-5.5\n"
        "created: 2026-10-05T09:12:00Z\n"
        "updated: 2026-10-05T09:12:00Z\n"
        'supersedes: "[[a]]"\n'
        'superseded_by: "[[b]]"\n'
        "aliases:\n"
        "  - style\n"
        "rating: 5\n"
        "---\n"
        "Body text.\n"
    )


def test_dump_omits_empty_known_fields_but_keeps_empty_unknown_ones():
    text = vault.dump({"type": "fact", "tags": [], "source": "", "x": "", "y": [], "z": {}}, "B")
    assert text == '---\ntype: fact\nx: ""\ny: []\nz: {}\n---\nB\n'


@pytest.mark.parametrize(
    "value",
    [
        "a: b",
        "x #y",
        "#x",
        " padded ",
        "- dash",
        "[[link]]",
        "{x}",
        'quote "inside"',
        "back\\slash",
        "line\nbreak",
        "tab\there",
        "*alias",
        "&anchor",
        "!tag",
        "|",
        "@at",
        "Ünïcödé ✓",
        "",
    ],
)
def test_dump_round_trips_awkward_strings(value):
    meta = {"title": value, "list": [value], "nested": {"k": value}}
    parsed, _, malformed = vault.parse(vault.dump(meta, "B"))
    assert not malformed
    assert parsed == meta


def test_dump_round_trips_nested_unknown_structures():
    meta = {"a": [{"x": "1", "y": ["p", "q"]}, ["r", "s"], {}, []], "b": {"c": {"d": "e"}}}
    parsed, _, _ = vault.parse(vault.dump(meta, "B"))
    assert parsed == meta


def test_plain_scalars_stay_plain():
    text = vault.dump({"id": "01J9XK3M7Q2R8S5T6V7W8X9Y0Z", "rating": "5", "when": "2026-10-05"}, "")
    assert '"' not in text


# Fields and defaults


def test_memory_defaults_for_a_hand_written_note(tmp_path):
    write(tmp_path, "projects/acme-site/Client contacts.md", "Main contact is Dana.\n")
    mtime = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC).timestamp()
    os.utime(tmp_path / "projects/acme-site/Client contacts.md", (mtime, mtime))
    memory = vault.read(tmp_path, "projects/acme-site/Client contacts.md")
    assert memory.stem == "Client contacts"
    assert memory.scope == "projects/acme-site"
    assert memory.id is None
    assert (memory.type, memory.status, memory.tags, memory.source) == ("note", "active", [], "")
    assert memory.created == memory.updated == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert memory.body == "Main contact is Dana."


def test_memory_field_normalization(tmp_path):
    write(
        tmp_path,
        "inbox/x.md",
        "---\nid: 01j9xk3m7q2r8s5t6v7w8x9y0z\ntype: PREFERENCE\nstatus: bogus\n"
        "tags: '#solo'\nupdated: 2026-10-05T11:00\n---\nB",
    )
    memory = vault.read(tmp_path, "inbox/x.md")
    assert memory.id == "01J9XK3M7Q2R8S5T6V7W8X9Y0Z"
    assert memory.type == "preference"
    assert memory.status == "pending"  # unknown status, under inbox/
    assert memory.tags == ["solo"]
    assert memory.updated == datetime(2026, 10, 5, 11, 0, tzinfo=UTC)


def test_unknown_type_and_status_fall_back(tmp_path):
    write(tmp_path, "global/x.md", "---\ntype: opinion\nstatus: archived\n---\nB")
    memory = vault.read(tmp_path, "global/x.md")
    assert (memory.type, memory.status) == ("note", "active")


def test_parse_time_formats():
    utc = datetime(2026, 10, 5, 9, 12, tzinfo=UTC)
    assert vault.parse_time("2026-10-05T09:12:00Z") == utc
    assert vault.parse_time("2026-10-05T11:12:00+02:00") == utc
    assert vault.parse_time("2026-10-05T09:12:00.250Z") == utc.replace(microsecond=250000)
    assert vault.parse_time("2026-10-05T09:12") == utc
    assert vault.parse_time("2026-10-05") == datetime(2026, 10, 5, tzinfo=UTC)
    assert vault.parse_time("last tuesday") is None
    assert vault.parse_time(["2026-10-05"]) is None


def test_recency_uses_the_later_of_updated_and_mtime(tmp_path):
    path = write(tmp_path, "global/x.md", "---\nupdated: 2020-01-01T00:00:00Z\n---\nB")
    memory = vault.read(tmp_path, "global/x.md")
    assert memory.recency == datetime.fromtimestamp(path.stat().st_mtime, UTC)


# Layout


def test_scan_finds_only_visible_markdown_and_flags_memories(tmp_path):
    for rel in [
        "README.md",
        "global/a.md",
        "global/sub/b.MD",
        "global/c.txt",
        "imports/chatgpt/d.md",
        "inbox/e.md",
        ".obsidian/f.md",
        ".lil-memory/trash/global/g.md",
        "global/.hidden.md",
        "global/.drafts/h.md",
    ]:
        write(tmp_path, rel, "x")
    found = {path: memory for path, _, memory in vault.scan(tmp_path)}
    assert found == {
        "README.md": False,
        "global/a.md": True,
        "global/sub/b.MD": True,
        "imports/chatgpt/d.md": False,
        "inbox/e.md": True,
    }


def test_scan_does_not_follow_directory_symlinks(tmp_path):
    write(tmp_path, "global/a.md", "x")
    os.symlink(tmp_path, tmp_path / "global" / "loop")
    assert [p for p, _, _ in vault.scan(tmp_path)] == ["global/a.md"]


@pytest.mark.parametrize(
    "content, slug",
    [
        (
            "Prefers British spelling and short paragraphs in client-facing copy.",
            "prefers-british-spelling-and-short-paragraphs",
        ),
        (
            "The acme site deploys to Cloudflare Pages since October.",
            "the-acme-site-deploys-to-cloudflare",
        ),
        ("Smörgåsbord café naïve", "smorgasbord-cafe-naive"),
        ("\n\n---\n# Title: here!\nmore", "title-here"),
        (
            "Supercalifragilisticexpialidocious-and-antidisestablishmentarianism-x",
            "supercalifragilisticexpialidocious-and",
        ),
        ("a" * 80, "a" * 60),
        ("東京で働いている", "memory"),
        ("!!!", "memory"),
    ],
)
def test_slugify(content, slug):
    assert vault.slugify(content) == slug


@pytest.mark.parametrize("scope", ["global", "projects/acme-site", "inbox", "Some Folder/x"])
def test_valid_scopes(scope):
    assert vault.validate_scope(scope) == scope


@pytest.mark.parametrize(
    "scope",
    ["", "/abs", "a//b", "a/", "../up", "a/./b", ".hidden", "a/.b", "a\\b", "imports", "imports/x"],
)
def test_invalid_scopes(scope):
    with pytest.raises(VaultError):
        vault.validate_scope(scope)


def test_invalid_tags():
    assert vault.validate_tags(["#a", "b/c"]) == ["a", "b/c"]
    with pytest.raises(VaultError):
        vault.validate_tags(["two words"])
    with pytest.raises(VaultError):
        vault.validate_tags(["#"])


# Operations


def test_create_writes_canonical_file(tmp_path):
    memory = vault.create(
        tmp_path, "Prefers British spelling.", "preference", tags=["writing"], source="test"
    )
    assert memory.path == "global/prefers-british-spelling.md"
    text = (tmp_path / memory.path).read_text()
    meta, body, _ = vault.parse(text)
    assert is_id(meta["id"])
    assert meta["created"] == meta["updated"]
    assert vault.parse_time(meta["created"]) is not None
    assert text == vault.dump(meta, "Prefers British spelling.")
    assert list(meta) == ["id", "type", "status", "tags", "source", "created", "updated"]
    assert (memory.type, memory.status, memory.tags, memory.source) == (
        "preference",
        "active",
        ["writing"],
        "test",
    )
    assert oct((tmp_path / memory.path).stat().st_mode & 0o777) == "0o644"


def test_create_makes_slugs_unique_across_the_vault_case_insensitively(tmp_path):
    write(tmp_path, "projects/x/Same-Words.md", "x")
    write(tmp_path, "imports/claude/same-words-2.md", "x")
    write(tmp_path, "Same-Words-3.md", "x")
    memory = vault.create(tmp_path, "Same words", "fact")
    assert memory.path == "global/same-words-4.md"


def test_create_never_overwrites_even_if_taken_check_misses(tmp_path):
    write(tmp_path, "global/dup.md", "original")
    memory = vault.create(tmp_path, "dup", "fact", taken=lambda stem: False)
    assert memory.path == "global/dup-2.md"
    assert (tmp_path / "global/dup.md").read_text() == "original"


def test_create_leaves_no_temp_files(tmp_path):
    vault.create(tmp_path, "one", "fact")
    vault.create(tmp_path, "one", "fact")
    assert sorted(p.name for p in (tmp_path / "global").iterdir()) == ["one-2.md", "one.md"]


def test_create_validates_input(tmp_path):
    with pytest.raises(VaultError):
        vault.create(tmp_path, "  ", "fact")
    with pytest.raises(VaultError):
        vault.create(tmp_path, "x", "opinion")
    with pytest.raises(VaultError):
        vault.create(tmp_path, "x", "fact", scope="../escape")
    assert not any(tmp_path.iterdir())


def test_supersede(tmp_path):
    old = vault.create(tmp_path, "Deploys to Netlify.", "decision", "projects/acme", ["hosting"])
    new, old_after = vault.supersede(tmp_path, old, "Deploys to Cloudflare Pages.", "test")
    assert new.path == "projects/acme/deploys-to-cloudflare-pages.md"
    assert (new.type, new.tags, new.status) == ("decision", ["hosting"], "active")
    assert new.meta["supersedes"] == "[[deploys-to-netlify]]"
    assert old_after.status == "superseded"
    assert old_after.meta["superseded_by"] == "[[deploys-to-cloudflare-pages]]"
    assert old_after.id == old.id
    assert old_after.body == "Deploys to Netlify."
    assert old_after.meta["created"] == old.meta["created"]


def test_supersede_with_the_same_content_gets_a_suffix(tmp_path):
    old = vault.create(tmp_path, "Same text", "fact")
    new, _ = vault.supersede(tmp_path, old, "Same text")
    assert new.stem == "same-text-2"


def test_supersede_preserves_unknown_fields_and_hand_edits(tmp_path):
    old = vault.create(tmp_path, "Original", "fact")
    path = tmp_path / old.path
    path.write_text(
        path.read_text().replace("---\nOriginal", "rating: 5\naliases: [x]\n---\nEdited by hand")
    )
    vault.supersede(tmp_path, old, "Replacement")  # `old` is stale; the file is re-read
    after = vault.read(tmp_path, old.path)
    assert after.body == "Edited by hand"
    assert after.meta["rating"] == "5"
    assert after.meta["aliases"] == ["x"]
    assert list(after.meta)[-2:] == ["rating", "aliases"]


def test_supersede_assigns_id_to_hand_written_note(tmp_path):
    write(tmp_path, "global/note.md", "No frontmatter")
    vault.supersede(tmp_path, vault.read(tmp_path, "global/note.md"), "Better")
    after = vault.read(tmp_path, "global/note.md")
    assert after.id is not None and after.status == "superseded"
    assert after.body == "No frontmatter"


def test_cannot_supersede_twice_or_malformed(tmp_path):
    old = vault.create(tmp_path, "Old", "fact")
    vault.supersede(tmp_path, old, "New")
    with pytest.raises(VaultError, match="already superseded by"):
        vault.supersede(tmp_path, vault.read(tmp_path, old.path), "Newer")
    write(tmp_path, "global/bad.md", "---\ntype: [oops\n---\nBody")
    with pytest.raises(VaultError, match="malformed"):
        vault.supersede(tmp_path, vault.read(tmp_path, "global/bad.md"), "Fix")
    assert (tmp_path / "global/bad.md").read_text() == "---\ntype: [oops\n---\nBody"


def test_rewrite_keeps_file_permissions(tmp_path):
    old = vault.create(tmp_path, "Old", "fact")
    os.chmod(tmp_path / old.path, 0o600)
    vault.supersede(tmp_path, old, "New")
    assert (tmp_path / old.path).stat().st_mode & 0o777 == 0o600


def test_forget_moves_to_trash_and_handles_collisions(tmp_path):
    first = vault.create(tmp_path, "Forget me", "fact", "projects/x")
    assert vault.forget(tmp_path, first.path) == ".lil-memory/trash/projects/x/forget-me.md"
    assert not (tmp_path / first.path).exists()
    second = vault.create(tmp_path, "Forget me", "fact", "projects/x")
    trashed = vault.forget(tmp_path, second.path)
    assert trashed.startswith(".lil-memory/trash/projects/x/forget-me-") and trashed.endswith(
        "Z.md"
    )
    assert (tmp_path / trashed).read_text() == vault.dump(second.meta, second.body)
