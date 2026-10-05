"""HTTP mode: MCP at a secret URL on 127.0.0.1, for remote clients reached through a tunnel."""

import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from lil_memory.cli import main

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
HEADERS = {"Accept": "application/json, text/event-stream"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def root(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    vault = tmp_path / "vault"
    main(["init", str(vault)])
    capsys.readouterr()
    return vault


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def http_server(root: Path, *extra: str):
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "lil_memory", "serve", "--http", "--port", str(port)]
        + ["--vault", str(root), *extra],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.time() + 20
        while True:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                assert proc.poll() is None, proc.stderr.read()
                assert time.time() < deadline, "server did not start"
                time.sleep(0.05)
        secret = (root / ".lil-memory/http-secret").read_text().strip()
        yield f"http://127.0.0.1:{port}", secret, proc
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def post_initialize(url: str, **headers: str) -> httpx.Response:
    return httpx.post(url, json=INITIALIZE, headers={**HEADERS, **headers}, timeout=10)


@pytest.mark.anyio
async def test_tools_work_over_http_at_the_secret_url(root):
    with http_server(root) as (base, secret, _):
        async with (
            streamable_http_client(f"{base}/mcp/{secret}") as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            assert len(tools.tools) == 6
            saved = await session.call_tool(
                "remember", {"content": "Saved from ChatGPT", "type": "fact"}
            )
            assert not saved.isError
            found = await session.call_tool("recall", {"query": "chatgpt"})
            assert "Saved from ChatGPT" in found.content[0].text
    assert (root / "global/saved-from-chatgpt.md").exists()


def test_everything_but_the_secret_url_is_not_found(root):
    with http_server(root) as (base, secret, _):
        assert post_initialize(f"{base}/mcp/{secret}").status_code == 200
        for path in ["/", "/mcp", "/mcp/", f"/mcp/{secret[:-1]}", f"/mcp/{secret}x", "/sse"]:
            assert post_initialize(base + path).status_code == 404, path
            assert httpx.get(base + path, timeout=10).status_code == 404, path


def test_tunnel_host_headers_are_accepted(root):
    with http_server(root) as (base, secret, _):
        response = post_initialize(
            f"{base}/mcp/{secret}",
            Host="quiet-river-1234.trycloudflare.com",
            Origin="https://chatgpt.com",
        )
        assert response.status_code == 200


def test_secret_is_private_ignored_by_git_and_stable(root):
    with http_server(root) as (_, first, proc):
        pass
    path = root / ".lil-memory/http-secret"
    assert len(first) >= 22  # 128 bits, URL-safe base64
    assert path.stat().st_mode & 0o777 == 0o600
    assert "http-secret" in (root / ".lil-memory/.gitignore").read_text().splitlines()
    assert f"/mcp/{first}" in proc.stderr.read()
    with http_server(root) as (_, second, _):
        pass
    assert second == first


def test_rotate_replaces_the_secret_and_the_old_url_stops_working(root):
    with http_server(root) as (_, old, _):
        pass
    with http_server(root, "--rotate") as (base, new, _):
        assert new != old
        assert post_initialize(f"{base}/mcp/{old}").status_code == 404
        assert post_initialize(f"{base}/mcp/{new}").status_code == 200


def test_old_vault_gitignore_gains_the_secret_line(root):
    ignore = root / ".lil-memory/.gitignore"
    ignore.write_text("index.sqlite*\n")
    with http_server(root):
        pass
    assert ignore.read_text() == "index.sqlite*\nhttp-secret\n"


def test_access_logs_do_not_print_the_secret(root):
    with http_server(root) as (base, secret, proc):
        post_initialize(f"{base}/mcp/{secret}")
    output = proc.stdout.read() + proc.stderr.read()
    assert output.count(secret) == 2  # only in the startup message (local and tunnel URL)


def test_rotate_requires_http(root, capsys):
    assert main(["serve", "--rotate", "--vault", str(root)]) == 1
    assert "--rotate only applies to --http" in capsys.readouterr().err
    assert not os.path.exists(root / ".lil-memory/http-secret")
