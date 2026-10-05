"""The frontmatter format: failsafe YAML parsing, canonical writing and timestamps.

Implements docs/SPEC.md §5, §6.1, §6.3 and §6.4.
"""

import json
import re
from datetime import UTC, datetime

import yaml

KEY_ORDER = tuple(
    ["id", "type", "status", "tags", "source", "created", "updated", "supersedes", "superseded_by"]
)

# BaseLoader gives the YAML failsafe schema: every scalar is a string (SPEC §6.1).
_Loader = getattr(yaml, "CBaseLoader", yaml.BaseLoader)
_CLOSING = re.compile(r"^---[ \t]*$", re.MULTILINE)
_PLAIN_KEY = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_ -]*")


def parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text[-1] in "Zz":
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def format_time(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(text: str) -> tuple[dict | None, str, bool]:
    """Split a file into (frontmatter, body, malformed). See SPEC §5 and §6.1."""
    text = text.removeprefix("﻿").replace("\r\n", "\n")
    first, _, rest = text.partition("\n")
    if first.rstrip(" \t") != "---":
        return None, text, False
    closing = _CLOSING.search(rest)
    if closing is None:
        return None, text, False
    raw, body = rest[: closing.start()], rest[closing.end() + 1 :]
    if not raw.strip():
        return {}, body, False
    try:
        data = yaml.load(raw, Loader=_Loader)
    except yaml.YAMLError:
        return None, body, True
    if not isinstance(data, dict):
        return None, body, True
    return data, body, False


def dump(meta: dict, body: str) -> str:
    """Serialize frontmatter canonically (SPEC §6.4) followed by the body."""
    keys = [k for k in KEY_ORDER if k in meta] + [k for k in meta if k not in KEY_ORDER]
    lines = []
    for key in keys:
        value = meta[key]
        if key in KEY_ORDER and value in ("", [], None):
            continue
        lines += _emit(f"{_key(key)}:", value, 0)
    head = "\n".join(lines) + "\n" if lines else ""
    return f"---\n{head}---\n{body.strip()}\n"


def _scalar(value: object) -> str:
    text = "" if value is None else str(value)
    if text and "\n" not in text:
        try:
            if yaml.load(f"k: {text}", Loader=_Loader) == {"k": text}:
                return text
        except yaml.YAMLError:
            pass
    return json.dumps(text, ensure_ascii=False)


def _key(key: object) -> str:
    text = str(key)
    return text if _PLAIN_KEY.fullmatch(text) and text.strip() == text else json.dumps(text)


def _emit(head: str, value: object, indent: int) -> list[str]:
    """Lines for one `key:` or `-` entry, in block style with two-space indents."""
    pad = " " * indent
    if isinstance(value, dict | list) and not value:
        return [f"{pad}{head} {'{}' if isinstance(value, dict) else '[]'}"]
    if isinstance(value, dict):
        lines = [ln for k, v in value.items() for ln in _emit(f"{_key(k)}:", v, indent + 2)]
        if head == "-":  # the first key shares the dash's line
            return [f"{pad}- {lines[0].lstrip()}", *lines[1:]]
        return [f"{pad}{head}", *lines]
    if isinstance(value, list):
        return [f"{pad}{head}", *(ln for item in value for ln in _emit("-", item, indent + 2))]
    return [f"{pad}{head} {_scalar(value)}"]
