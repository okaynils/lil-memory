import pytest


@pytest.fixture(autouse=True)
def cache_dir(tmp_path_factory, monkeypatch):
    """Keep every index out of the real user cache; subprocesses inherit the variable."""
    cache = tmp_path_factory.mktemp("cache")
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    return cache
