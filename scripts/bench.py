"""Check lil memory's performance budgets on a synthetic vault.

    uv run python scripts/bench.py [--memories 10000] [--keep]

Exits 0 when every budget is met, 1 otherwise. Budgets (from CLAUDE.md):

    server cold start  < 500 ms   median of 5 launches, process start to `initialize` reply
    recall             <  50 ms   95th percentile over 40 queries, warm index
    remember           <  30 ms   95th percentile over 40 writes, including the index update
    full reindex       <   5 s    one run
    refresh, no change < 100 ms   median of 5 scans
"""

import argparse
import asyncio
import json
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from lil_memory.frontmatter import dump, format_time
from lil_memory.ids import new_id
from lil_memory.index import Index
from lil_memory.server import build
from lil_memory.vault import TYPES

COMMON = (
    "the of and to in is that for it as with was on be at by this had not are but from or have "
    "an they which one you were all we her she there would their will when who him been has "
    "more if no out do so can what up said about other into than its time only could new them "
    "man some these then two first may any like now my such make over our even most me state "
    "after also made many did must before back see through way where get much go well your know"
).split()


def make_words(rng: random.Random) -> list[str]:
    letters = "abcdefghijklmnopqrstuvwxyz"
    rare = {"".join(rng.choices(letters, k=rng.randint(4, 10))) for _ in range(4000)}
    return COMMON + sorted(rare)


def sentence(rng: random.Random, words: list[str], weights: list[float]) -> str:
    return " ".join(rng.choices(words, weights, k=rng.randint(10, 80))).capitalize() + "."


def generate(root: Path, count: int, rng: random.Random) -> list[str]:
    words = make_words(rng)
    weights = [1 / (rank + 1) for rank in range(len(words))]  # Zipf-like, like real text
    scopes = ["global"] + [f"projects/p{i}" for i in range(20)] + ["work/notes", "work/people"]
    tags = rng.sample(words[len(COMMON) :], 50)
    now = time.time()
    for directory in scopes:
        (root / directory).mkdir(parents=True, exist_ok=True)
    for i in range(count):
        when = format_time_epoch(now - rng.uniform(0, 3 * 365 * 86400))
        meta = {
            "id": new_id(),
            "type": rng.choice(TYPES),
            "status": "superseded" if rng.random() < 0.1 else "active",
            "tags": rng.sample(tags, rng.randint(0, 3)),
            "source": "bench",
            "created": when,
            "updated": when,
        }
        body = sentence(rng, words, weights)
        (root / rng.choice(scopes) / f"memory-{i}.md").write_text(dump(meta, body))
    (root / ".lil-memory").mkdir()
    (root / ".lil-memory/config.toml").write_text('format = "0.1"\n')
    return words


def format_time_epoch(seconds: float) -> str:
    return format_time(datetime.fromtimestamp(seconds, UTC))


def timed(fn) -> float:
    start = time.perf_counter()
    fn()
    return (time.perf_counter() - start) * 1000


def p95(samples: list[float]) -> float:
    return statistics.quantiles(samples, n=20)[-1]


def cold_start(root: Path) -> float:
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "bench", "version": "0"},
        },
    }
    start = time.perf_counter()
    proc = subprocess.Popen(
        [sys.executable, "-m", "lil_memory", "serve", "--vault", str(root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    proc.stdin.write(json.dumps(request) + "\n")
    proc.stdin.flush()
    reply = proc.stdout.readline()
    elapsed = (time.perf_counter() - start) * 1000
    proc.kill()
    proc.wait()
    assert '"serverInfo"' in reply, reply
    return elapsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--memories", type=int, default=10_000)
    parser.add_argument("--keep", action="store_true", help="keep the generated vault")
    args = parser.parse_args()
    rng = random.Random(42)
    root = Path(tempfile.mkdtemp(prefix="lil-memory-bench-"))
    try:
        print(f"Generating {args.memories} memories in {root} ...")
        words = generate(root, args.memories, rng)
        results = []

        index = Index(root)
        reindex_s = timed(index.reindex) / 1000
        results.append(("full reindex", reindex_s, 5.0, "s"))
        refresh = [timed(lambda: index.refresh(force=True)) for _ in range(5)]
        results.append(("refresh, no change", statistics.median(refresh), 100.0, "ms"))
        index.close()

        mcp = build(root)

        def call(tool: str, **kwargs) -> None:
            asyncio.run(mcp.call_tool(tool, kwargs))

        call("recall", query="warm up")  # first read tool call refreshes the index
        queries = [" ".join(rng.sample(words[:400], rng.randint(1, 4))) for _ in range(40)]
        recall = [timed(lambda q=q: call("recall", query=q)) for q in queries]
        results.append(("recall", p95(recall), 50.0, "ms"))
        remember = [
            timed(lambda i=i, q=q: call("remember", content=f"Bench memory {i}: {q}", type="fact"))
            for i, q in enumerate(queries)
        ]
        results.append(("remember", p95(remember), 30.0, "ms"))
        time.sleep(2.1)  # let the refresh window expire, as between two turns of a chat
        with_scan = timed(lambda: call("recall", query=queries[0]))

        cold_start(root)  # compile bytecode once, as an installed package would have
        starts = [cold_start(root) for _ in range(5)]
        results.append(("server cold start", statistics.median(starts), 500.0, "ms"))
    finally:
        if args.keep:
            print(f"Kept vault at {root}")
        else:
            shutil.rmtree(root)

    print(f"\n{'budget':<20}{'measured':>12}{'limit':>10}")
    ok = True
    for name, value, limit, unit in results:
        passed = value < limit
        ok &= passed
        print(
            f"{name:<20}{value:>9.1f} {unit:<2}{limit:>7.0f} {unit:<2} {'ok' if passed else 'OVER'}"
        )
    print(f"(info) recall that also runs the 2 s mtime scan: {with_scan:.1f} ms")
    print("\nAll performance budgets met." if ok else "\nSome budgets were missed.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
