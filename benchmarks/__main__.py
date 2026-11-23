"""
TypeDAL benchmark suite.

    python -m benchmarks pg start                    # pinned Postgres container on port 9640
    python -m benchmarks run --profile full --out results/run.json
    python -m benchmarks run --profile quick --backend sqlite --case select_full
    python -m benchmarks check                       # one verified repetition per cell, no timing value
    python -m benchmarks summary results/run.json
    python -m benchmarks compare base.json cand.json --md out.md
"""

import argparse
import datetime as dt
import importlib.metadata
import json
import os
import platform
import socket
import subprocess
import sys
import tempfile
import time
import typing as t
from pathlib import Path

PG_CONTAINER = "typedal-bench-pg"
PG_IMAGE = "postgres:16"
PG_PORT = 9640
PG_URI = f"postgres://bench:bench@localhost:{PG_PORT}"
REPO = Path(__file__).resolve().parent.parent


def _sh(*cmd: str) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout.strip()
    except OSError:
        return ""


# --- postgres --------------------------------------------------------------------------------


def _docker() -> str:
    return os.environ.get("BENCH_DOCKER", "docker")


def pg_start() -> None:
    if _sh(_docker(), "ps", "-q", "-f", f"name=^{PG_CONTAINER}$"):
        print(f"{PG_CONTAINER} already running")
    else:
        subprocess.run([_docker(), "rm", "-f", PG_CONTAINER], capture_output=True)
        subprocess.run(
            [
                _docker(), "run", "-d", "--name", PG_CONTAINER, "-p", f"{PG_PORT}:5432",
                "-e", "POSTGRES_USER=bench", "-e", "POSTGRES_PASSWORD=bench", "-e", "POSTGRES_DB=bench",
                PG_IMAGE,
            ],
            check=True,
        )  # fmt: skip
    for _ in range(60):
        try:
            import psycopg2

            psycopg2.connect(f"{PG_URI}/bench").close()
            print(f"postgres ready at {PG_URI}")
            return
        except Exception:
            time.sleep(1)
    raise SystemExit("postgres did not become ready")


def pg_stop() -> None:
    subprocess.run([_docker(), "rm", "-f", PG_CONTAINER], check=False)


def pg_recreate_databases(uri: str) -> dict[str, t.Any]:
    """Drop and create the benchmark databases, so every run starts from the same empty state."""
    import psycopg2

    conn = psycopg2.connect(f"{uri.rstrip('/')}/postgres")
    conn.autocommit = True
    cur = conn.cursor()
    for name in ("bench_pydal", "bench_typedal"):
        cur.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        cur.execute(f"CREATE DATABASE {name}")
    cur.execute("SELECT version()")
    info: dict[str, t.Any] = {"version": cur.fetchone()[0]}
    for setting in ("shared_buffers", "work_mem", "fsync", "synchronous_commit", "max_connections", "jit"):
        cur.execute(f"SHOW {setting}")
        info[setting] = cur.fetchone()[0]
    conn.close()
    return info


# --- metadata --------------------------------------------------------------------------------


def environment() -> dict[str, t.Any]:
    import sqlite3

    import pydal

    import typedal

    cpu = ""
    try:
        cpu = next(
            line.split(":", 1)[1].strip()
            for line in Path("/proc/cpuinfo").read_text().splitlines()
            if line.startswith("model name")
        )
    except (OSError, StopIteration):
        pass
    mem = ""
    try:
        mem = Path("/proc/meminfo").read_text().splitlines()[0].split(":", 1)[1].strip()
    except OSError:
        pass
    return {
        "timestamp": dt.datetime.now(dt.UTC).isoformat(),
        "host": socket.gethostname(),
        "python": sys.version,
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "cpu": cpu,
        "cpu_count": os.cpu_count(),
        "memory": mem,
        "loadavg": os.getloadavg(),
        "pydal": {"version": pydal.__version__, "path": str(Path(pydal.__file__).parent)},
        "typedal": {"version": importlib.metadata.version("typedal"), "path": str(Path(typedal.__file__).parent)},
        "sqlite": sqlite3.sqlite_version,
        "git": {
            "sha": _sh("git", "-C", str(REPO), "rev-parse", "HEAD"),
            "dirty": bool(_sh("git", "-C", str(REPO), "status", "--porcelain", "--untracked-files=no")),
            "branch": _sh("git", "-C", str(REPO), "rev-parse", "--abbrev-ref", "HEAD"),
        },
    }


# --- commands --------------------------------------------------------------------------------


def _settings(args: argparse.Namespace) -> dict[str, t.Any]:
    from .runner import PROFILES

    prof = dict(PROFILES[args.profile])
    return {
        "profile": args.profile,
        "sizes": args.sizes or prof["sizes"],
        "cases": args.case,
        "variants": args.variant,
        "min_reps": args.min_reps or prof["min_reps"],
        "max_reps": args.max_reps or prof["max_reps"],
        "budget_s": prof["budget_s"] if args.budget_s is None else args.budget_s,
        "warmup": prof.get("warmup"),
        "memory": not args.no_memory and args.profile != "check",
    }


def cmd_backend(args: argparse.Namespace) -> None:
    """Internal: one backend in a fresh process (no state shared between backends)."""
    from .runner import Settings, run_backend

    st = Settings(**json.loads(args.settings))
    results = run_backend(args.backend, args.pg_uri, st, lambda m: print(m, flush=True))
    Path(args.json_out).write_text(json.dumps(results))


def cmd_run(args: argparse.Namespace) -> int:
    settings = _settings(args)
    meta: dict[str, t.Any] = {"environment": environment(), "settings": settings, "backends": {}}
    meta["git"] = meta["environment"]["git"]
    if meta["git"]["dirty"]:
        print("WARNING: tracked files have uncommitted changes; results are not reproducible from a commit")
    results: list[dict[str, t.Any]] = []
    for backend in args.backend:
        info: dict[str, t.Any] = {}
        if backend == "postgres":
            info = pg_recreate_databases(args.pg_uri)
        else:
            import sqlite3

            info = {"version": sqlite3.sqlite_version, "storage": "file in a fresh temp dir, pydal defaults"}
        meta["backends"][backend] = info
        print(f"== {backend} ({info.get('version', '')[:60]})", flush=True)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            out = f.name
        cmd = [
            sys.executable, "-m", "benchmarks", "_backend", "--backend", backend,
            "--pg-uri", args.pg_uri, "--settings", json.dumps(settings), "--json-out", out,
        ]  # fmt: skip
        t0 = time.time()
        proc = subprocess.run(cmd, cwd=REPO)
        info["duration_s"] = round(time.time() - t0, 1)
        if proc.returncode != 0:
            print(f"backend {backend} crashed (exit {proc.returncode})")
            results.append({"backend": backend, "case": "*", "variant": "*", "n": 0, "verified": False,
                            "error": f"process exited with {proc.returncode}"})  # fmt: skip
            continue
        results += json.loads(Path(out).read_text())
        os.unlink(out)
    payload = {"meta": meta, "results": results}
    out_path = Path(args.out or f"benchmarks/results/{args.profile}-{dt.datetime.now():%Y%m%d-%H%M%S}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=1))
    failed = [r for r in results if not r.get("verified")]
    print(f"\nwrote {out_path} ({len(results)} cells, {len(failed)} failed)")
    for r in failed:
        print(f"  FAILED {r['backend']}/{r['case']}/{r['variant']}/n={r['n']}: {r.get('error')}")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m benchmarks", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)  # fmt: skip
    sub = parser.add_subparsers(dest="cmd", required=True)

    for name in ("run", "check"):
        p = sub.add_parser(name)
        p.add_argument("--backend", nargs="+", default=["sqlite", "postgres"], choices=["sqlite", "postgres"])
        p.add_argument("--pg-uri", default=os.environ.get("BENCH_PG_URI", PG_URI))
        p.add_argument("--case", nargs="+")
        p.add_argument("--variant", nargs="+")
        p.add_argument("--sizes", nargs="+", type=int)
        p.add_argument("--min-reps", type=int)
        p.add_argument("--max-reps", type=int)
        p.add_argument("--budget-s", type=float)
        p.add_argument("--no-memory", action="store_true")
        p.add_argument("--out")
        if name == "run":
            p.add_argument("--profile", default="full", choices=["full", "large", "quick", "check"])

    p = sub.add_parser("_backend")
    p.add_argument("--backend", required=True)
    p.add_argument("--pg-uri")
    p.add_argument("--settings", required=True)
    p.add_argument("--json-out", required=True)

    p = sub.add_parser("pg")
    p.add_argument("action", choices=["start", "stop"])

    p = sub.add_parser("summary")
    p.add_argument("file")
    p.add_argument("--md")

    p = sub.add_parser("compare")
    p.add_argument("baseline")
    p.add_argument("candidate")
    p.add_argument("--md")

    args = parser.parse_args(argv)
    if args.cmd == "check":
        args.profile = "check"
        args.out = args.out or str(Path(tempfile.gettempdir()) / "typedal-bench-check.json")
        return cmd_run(args)
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "_backend":
        cmd_backend(args)
        return 0
    if args.cmd == "pg":
        pg_start() if args.action == "start" else pg_stop()
        return 0
    if args.cmd in ("summary", "compare"):
        from . import compare

        text = compare.summary(args.file) if args.cmd == "summary" else compare.compare(args.baseline, args.candidate)
        if args.md:
            Path(args.md).write_text(text)
            print(f"wrote {args.md}")
        else:
            print(text)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
