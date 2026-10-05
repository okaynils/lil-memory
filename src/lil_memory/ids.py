"""ULID generation (docs/SPEC.md §7). Stdlib only."""

import os
import re
import time

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ULID = re.compile(r"[0-7][0-9A-HJKMNP-TV-Z]{25}", re.IGNORECASE)


def new_id(ms: int | None = None) -> str:
    """Return a new ULID: 48 bits of milliseconds, then 80 random bits."""
    if ms is None:
        ms = time.time_ns() // 1_000_000
    value = (ms << 80) | int.from_bytes(os.urandom(10), "big")
    chars = []
    for _ in range(26):
        chars.append(ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def is_id(value: object) -> bool:
    return isinstance(value, str) and _ULID.fullmatch(value) is not None
