# TypeDAL benchmark suite

Measures TypeDAL against plain pydal on SQLite and PostgreSQL across reads, writes, joins, hydration and the cold
path, with correctness verification on every measured repetition.

```bash
./venv/bin/python -m benchmarks pg start                     # postgres:16 container "typedal-bench-pg" on port 9640
./venv/bin/python -m benchmarks check                        # one verified repetition per cell, both backends
./venv/bin/python -m benchmarks run --profile full --out results.json   # 'large' adds 100k rows
./venv/bin/python -m benchmarks run --profile quick --backend sqlite --case select_full insert_loop
./venv/bin/python -m benchmarks summary results.json         # medians + cost relative to plain pydal
./venv/bin/python -m benchmarks compare base.json cand.json  # speedup matrix with noise classification
./venv/bin/python -m benchmarks pg stop
```

`--backend`, `--case`, `--variant`, `--sizes`, `--min-reps`, `--max-reps`, `--budget-s`, `--no-memory` narrow or
adjust a run. Set `BENCH_PG_URI` (or `--pg-uri`) to use another server; `bench_pydal` and `bench_typedal` databases
are dropped and recreated there at the start of every run.

## Schema and data

Two tables, defined identically in pydal (`schema.define_pydal`) and as TypeDAL models (`schema.make_models`):

- `bench_author(name, email)`, with a `posts` one-to-many relationship in TypeDAL
- `bench_post(title, views:int, score:double, published:bool, created_at:datetime, author:reference, body:text)`

Data is generated deterministically (`random.Random(42)`): ten posts per author, even-indexed posts are published.
Seeding uses multi-row `INSERT` statements encoded by pydal's own adapter and bypasses both ORMs; it is never timed.
Tables are emptied and their id sequences restarted before every seed, so ids are always `1..n`.

## Variants

| variant | what runs |
|---|---|
| `pydal` | a separate plain `pydal.DAL` instance with its own database: the reference baseline |
| `mixed` | the TypeDAL instance used with pydal syntax (`db.bench_post`, `db(query).select()`), or a TypeDAL query builder fed a pydal query |
| `typedal` | the TypeDAL model API (`Post.where(...).collect()`, `Post.insert(...)`, ...) |
| `typedal_execute` | TypeDAL's query builder returning raw pydal rows (`.execute()`): query building without hydration |

## Cases

| case | op | timed operation | sizes |
|---|---|---|---|
| `select_full` | read | all N rows, all columns | all |
| `select_columns` | read | all N rows, 3 columns | all |
| `select_filtered` | read | table of 2N rows, `published == True` returns N | all |
| `select_by_id` | read | N primary-key lookups (`db.t(id)` / `Model(id)`) | per-row cap |
| `join_many_to_one` | join | N posts with their author (left join) | all |
| `join_one_to_many` | join | N/10 authors with their N posts (left join, grouped by TypeDAL) | all |
| `hydrate` | hydrate | `TypedRows.from_rows` over N pre-fetched rows (no query) | all |
| `insert_bulk` | insert | one `bulk_insert` of N rows + commit (pydal loops per row on these backends) | per-row cap |
| `insert_loop` | insert | N × `insert()` + commit | per-row cap |
| `insert_validated` | insert | N × `validate_and_insert()` with default validators + commit | per-row cap |
| `update_query` | update | table of 2N, one `UPDATE views = views + 1 WHERE published` hitting N + commit | all |
| `update_records` | update | `update_record()` on N fetched rows/instances + commit | per-row cap |
| `delete_query` | delete | table of 2N, one `DELETE WHERE published` removing N + commit | all |
| `connect_and_define` | schema | cold path: new DB instance on the existing database + define both tables | 1 |

Sizes in the `full` profile: 1, 10, 100, 1k, 10k; the `large` profile adds 100k (needs several GB of free memory;
only per-row-capped cases skip it). Per-row cases (one statement per row) stop at 10k on SQLite
and 1k on PostgreSQL: they scale linearly and on PostgreSQL every row is a network round trip.

## Correctness

- Reads compute a checksum over every selected column of every row **inside the timed region** (so lazy work cannot
  hide its cost) and compare it with the checksum of the generated data.
- Writes are checked afterwards with SQL aggregates (count, sum of ids, sum of views, published count) and with the
  operation's return value (number of ids/rows).
- `validate_and_insert` must report no errors; joins must return every post with its author's name, and the
  one-to-many case must yield every author exactly once.
- Any mismatch or exception marks the cell `verified: false` with the error; it is recorded, never skipped.

## Measurement

- Every backend runs in its own fresh process; each cell is: untimed prepare → `gc.collect()` → timed call → untimed
  verification. Read cases seed once per cell; mutating cases re-seed before every repetition.
- Warm-up: 2 repetitions for n ≤ 100, otherwise 1. The first warm-up time is kept as `first_ms` (the cold-ish path).
- Measured repetitions (`full`/`large`): at least 5 and at most 30, stopping after 1 s of measuring once the minimum is met.
  Cells whose warm-up takes over 1 s run 3 measured repetitions.
- Wall time (`perf_counter_ns`) and process CPU time (`process_time_ns`) per repetition; stats are median, mean,
  min/max, quartiles (≥ 4 reps), stdev and p95 (≥ 20 reps), plus rows/s.
- Peak Python memory: one extra, unmeasured-for-time repetition under `tracemalloc` at each case's largest size
  (when ≥ 1000; `--no-memory` to skip). It measures Python allocations only, not driver/C memory.
- Each result file records the git SHA and dirty state, Python, pydal/TypeDAL versions and paths, SQLite version,
  PostgreSQL version and settings, CPU, memory, load average and the run settings.

`compare` reports speedup = baseline median / candidate median per cell, and calls a change real only when it is at
least 3% and either a Mann-Whitney U test gives p < 0.01 or the two samples do not overlap; anything else is `~`
(noise). The geometric mean it prints is indicative only.

## Configuration

- SQLite: a file database in a fresh temporary directory per run, pydal defaults (pool size 0 for pydal, 1 for TypeDAL).
- PostgreSQL: `postgres:16` with the image defaults (fsync and synchronous commit on), psycopg2 driver, accessed over
  the Docker port mapping.
- TypeDAL is constructed with `use_pyproject=False, use_env=False` so a checkout's own config cannot change behaviour.

## Known limitations

- Single machine, no CPU pinning: run nothing else heavy during a measurement and compare against a baseline measured
  in the same session where possible (`compare` A/A runs of the baseline show the noise floor).
- Write timings include the commit, so they include disk sync on both backends.
- `hydrate` measures `TypedRows.from_rows`, which is what `collect()` uses for plain selects; joins hydrate through
  their own path and are covered by the join cases.
- Async APIs, caching (`.cache()`), pagination and `chunk` are not covered.
