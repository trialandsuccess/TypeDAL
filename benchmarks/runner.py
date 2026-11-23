"""Runs the cases for one backend in this process and returns raw measurements."""

import gc
import statistics
import tempfile
import time
import tracemalloc
import typing as t
from dataclasses import dataclass
from pathlib import Path

from . import schema
from .cases import CASES, Case, Ctx

PROFILES: dict[str, dict[str, t.Any]] = {
    # official scoring profile
    "full": {"sizes": [1, 10, 100, 1_000, 10_000], "min_reps": 5, "max_reps": 30, "budget_s": 1.0},
    # full plus 100k rows, for machines with enough memory (several GB free)
    "large": {"sizes": [1, 10, 100, 1_000, 10_000, 100_000], "min_reps": 5, "max_reps": 30, "budget_s": 1.0},
    # fast iteration while developing; not for claims
    "quick": {"sizes": [100, 10_000], "min_reps": 3, "max_reps": 10, "budget_s": 0.3},
    # one verified repetition per cell: a correctness smoke test, no timing value
    "check": {"sizes": [1, 10, 1_000], "min_reps": 1, "max_reps": 1, "budget_s": 0.0, "warmup": 0},
}
SLOW_CELL_S = 2.0  # cells whose warm-up exceeds this run only SLOW_MIN_REPS measured repetitions
SLOW_MIN_REPS = 3
MEMORY_MIN_N = 1_000


@dataclass
class Settings:
    profile: str
    sizes: list[int]
    cases: list[str] | None
    variants: list[str] | None
    min_reps: int
    max_reps: int
    budget_s: float
    warmup: int | None
    memory: bool


def make_ctx(backend: str, pg_uri: str | None, workdir: Path) -> Ctx:
    uris, folders = {}, {}
    for kind in ("pydal", "typedal"):
        folder = workdir / kind
        folder.mkdir(parents=True, exist_ok=True)
        folders[kind] = str(folder)
        if backend == "sqlite":
            uris[kind] = f"sqlite://{folder}/bench.sqlite"
        else:
            assert pg_uri, "postgres backend needs --pg-uri"
            uris[kind] = f"{pg_uri.rstrip('/')}/bench_{kind}"
    pdb = schema.new_pydal(uris["pydal"], folders["pydal"])
    schema.define_pydal(pdb)
    tdb = schema.new_typedal(uris["typedal"], folders["typedal"])
    author, post = schema.make_models()
    tdb.define(author)
    tdb.define(post)
    pdb.commit()
    tdb.commit()
    return Ctx(backend, pdb, tdb, author, post, uris, folders)


def _stats(samples_ns: list[int]) -> dict[str, float]:
    ms = sorted(s / 1e6 for s in samples_ns)
    out = {"median_ms": statistics.median(ms), "min_ms": ms[0], "max_ms": ms[-1], "mean_ms": statistics.fmean(ms)}
    if len(ms) >= 4:
        q = statistics.quantiles(ms, n=4)
        out |= {"q1_ms": q[0], "q3_ms": q[2]}
    if len(ms) >= 2:
        out["stdev_ms"] = statistics.stdev(ms)
    if len(ms) >= 20:
        out["p95_ms"] = statistics.quantiles(ms, n=20)[18]
    return out


def run_cell(
    ctx: Ctx, case: Case, variant: str, n: int, st: Settings, log: t.Callable[[str], None]
) -> dict[str, t.Any]:
    fn = case.variants[variant]
    db = ctx.db_for(variant)
    state = None if case.mutating else case.prepare(ctx, db, n)

    def once() -> tuple[int, int]:
        nonlocal state
        if case.mutating:
            state = case.prepare(ctx, db, n)
        gc.collect()
        c0, t0 = time.process_time_ns(), time.perf_counter_ns()
        result = fn(ctx, state)
        t1, c1 = time.perf_counter_ns(), time.process_time_ns()
        case.verify(ctx, db, state, result)  # raises on wrong results
        del result
        return t1 - t0, c1 - c0

    warmup = st.warmup if st.warmup is not None else (2 if n <= 100 else 1)
    first_ns = None
    for _ in range(warmup):
        wall, _cpu = once()
        first_ns = first_ns or wall
    min_reps = st.min_reps
    if first_ns and first_ns / 1e9 > SLOW_CELL_S:
        min_reps = min(min_reps, SLOW_MIN_REPS)

    wall_ns: list[int] = []
    cpu_ns: list[int] = []
    started = time.perf_counter()
    while len(wall_ns) < st.max_reps:
        if len(wall_ns) >= min_reps and time.perf_counter() - started >= st.budget_s:
            break
        wall, cpu = once()
        wall_ns.append(wall)
        cpu_ns.append(cpu)

    peak = None
    if st.memory and n >= MEMORY_MIN_N:
        if case.mutating:
            state = case.prepare(ctx, db, n)
        gc.collect()
        tracemalloc.start()
        result = fn(ctx, state)
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        case.verify(ctx, db, state, result)
        del result

    stats = _stats(wall_ns)
    stats["cpu_median_ms"] = statistics.median(cpu_ns) / 1e6
    stats["rows_per_s"] = n / (stats["median_ms"] / 1e3) if stats["median_ms"] else None
    log(f"  {case.name:<20} {variant:<16} n={n:<7} median {stats['median_ms']:10.3f} ms  ({len(wall_ns)} reps)")
    return {
        "backend": ctx.backend,
        "case": case.name,
        "op": case.op,
        "variant": variant,
        "n": n,
        "warmup": warmup,
        "first_ms": first_ns / 1e6 if first_ns else None,
        "wall_ns": wall_ns,
        "cpu_ns": cpu_ns,
        "peak_mem_bytes": peak,
        "stats": stats,
        "verified": True,
    }


def run_backend(backend: str, pg_uri: str | None, st: Settings, log: t.Callable[[str], None]) -> list[dict[str, t.Any]]:
    results = []
    with tempfile.TemporaryDirectory(prefix=f"typedal-bench-{backend}-") as tmp:
        ctx = make_ctx(backend, pg_uri, Path(tmp))
        try:
            for case in CASES:
                if st.cases and case.name not in st.cases:
                    continue
                sizes = case.sizes or st.sizes
                for n in sizes:
                    if case.max_n and n > case.max_n.get(backend, n):
                        continue
                    for variant in case.variants:
                        if st.variants and variant not in st.variants:
                            continue
                        try:
                            results.append(run_cell(ctx, case, variant, n, st, log))
                        except Exception as e:  # recorded, never skipped silently
                            log(f"  {case.name:<20} {variant:<16} n={n:<7} FAILED: {type(e).__name__}: {e}")
                            for db in (ctx.pdb, ctx.tdb):
                                try:
                                    db.rollback()
                                except Exception:
                                    pass
                            results.append(
                                {
                                    "backend": backend,
                                    "case": case.name,
                                    "op": case.op,
                                    "variant": variant,
                                    "n": n,
                                    "verified": False,
                                    "error": f"{type(e).__name__}: {e}",
                                }
                            )
        finally:
            ctx.pdb.close()
            ctx.tdb.close()
    return results
