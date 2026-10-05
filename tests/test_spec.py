"""Keep docs/SPEC.md honest: its examples must be exactly what the code reads and writes."""

import re
from pathlib import Path

import pytest

from lil_memory import vault
from lil_memory.frontmatter import KEY_ORDER

SPEC = (Path(__file__).parent.parent / "docs" / "SPEC.md").read_text()
EXAMPLES = SPEC[SPEC.index("## 12. Examples") : SPEC.index("## 13.")]
# Each example file is introduced by a line naming its path in backticks.
FILES = re.findall(r"`([^`]+\.md)`[^\n]*:\n\n```markdown\n(.*?)```", EXAMPLES, re.DOTALL)


def test_examples_were_found():
    assert len(FILES) == 5


@pytest.mark.parametrize("path, text", [f for f in FILES if f[1].startswith("---")])
def test_example_memories_are_canonical(path, text):
    meta, body, malformed = vault.parse(text)
    assert not malformed
    assert vault.dump(meta, body) == text


@pytest.mark.parametrize("path, text", FILES)
def test_example_filenames_follow_the_slug_algorithm(path, text):
    _, body, _ = vault.parse(text)
    stem = Path(path).stem
    if stem != "Client contacts":  # the hand-written note keeps its human filename
        assert stem == vault.slugify(body)


def test_slug_example_in_section_4():
    content, slug = re.search(r"Example: `([^`]+)` becomes `([^`]+)`", SPEC).groups()
    assert vault.slugify(content) == slug


def test_key_order_matches_section_6_4():
    listed = re.search(r"\*\*Key order:\*\* (.+?)\. All unknown", SPEC).group(1)
    assert tuple(re.findall(r"`([a-z_]+)`", listed)) == KEY_ORDER


def test_types_and_statuses_match_section_6_2():
    row = re.search(r"\| `type` \| (.+?) \|", SPEC).group(1)
    assert tuple(re.findall(r"`([a-z]+)`", row)) == vault.TYPES
    row = re.search(r"\| `status` \| (.+?) \|", SPEC).group(1)
    assert tuple(re.findall(r"`([a-z]+)`", row)) == vault.STATUSES


def test_config_format_matches_section_2_3():
    assert f'`format = "{vault.FORMAT}"`' in SPEC
