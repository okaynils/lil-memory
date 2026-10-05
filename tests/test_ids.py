from lil_memory.ids import ALPHABET, is_id, new_id


def test_new_id_shape():
    value = new_id()
    assert len(value) == 26
    assert set(value) <= set(ALPHABET)
    assert value[0] in "01234567"
    assert is_id(value)


def test_timestamp_prefix_encodes_milliseconds():
    # 2026-10-05T09:12:00Z in ms; the first 10 chars encode the 48-bit time.
    ms = 1_791_191_520_000
    prefix = new_id(ms)[:10]
    decoded = 0
    for char in prefix:
        decoded = decoded * 32 + ALPHABET.index(char)
    assert decoded == ms


def test_ids_sort_by_time_and_are_unique():
    assert new_id(1000) < new_id(2000)
    assert len({new_id() for _ in range(1000)}) == 1000


def test_is_id():
    assert is_id("01J9XK3M7Q2R8S5T6V7W8X9Y0Z")
    assert is_id("01j9xk3m7q2r8s5t6v7w8x9y0z")  # case-insensitive
    assert not is_id("81J9XK3M7Q2R8S5T6V7W8X9Y0Z")  # overflows 128 bits
    assert not is_id("01J9XK3M7Q2R8S5T6V7W8X9Y0I")  # I is not in the alphabet
    assert not is_id("01J9XK3M7Q2R8S5T6V7W8X9Y0")
    assert not is_id(None)
