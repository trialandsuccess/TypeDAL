"""
Official benchmark cases.

Every case has an untimed `prepare` (fresh, deterministic database state), one timed callable per
API variant, and an untimed `verify` that fails the run when the result is wrong.

Variants:
- pydal:           a plain `pydal.DAL` instance (reference baseline, own database)
- mixed:           the TypeDAL instance used with pydal syntax (`db.table`, `db(query).select()`)
- typedal:         the TypeDAL model API
- typedal_execute: TypeDAL's query builder returning raw pydal rows (`.execute()`), i.e. without hydration

Results of reads are consumed (every selected column of every row is read) inside the timed region,
so deferring work until access cannot hide its cost.
"""

import math
import typing as t
from dataclasses import dataclass, field

from pydal import DAL

from typedal import TypeDAL
from typedal.rows import TypedRows

from . import schema

# per-row cases (one statement per row; pydal bulk_insert is a loop too) stop at this size per backend:
# they scale linearly, and on postgres every row is a network round trip
LOOP_CAP = {"sqlite": 10_000, "postgres": 1_000}


@dataclass
class Ctx:
    backend: str
    pdb: DAL
    tdb: TypeDAL
    Author: t.Any
    Post: t.Any
    uris: dict[str, str]
    folders: dict[str, str]

    def db_for(self, variant: str) -> DAL:
        return self.pdb if variant == "pydal" else self.tdb


@dataclass
class Case:
    name: str
    op: str
    description: str
    prepare: t.Callable[[Ctx, DAL, int], dict[str, t.Any]]
    variants: dict[str, t.Callable[[Ctx, dict[str, t.Any]], t.Any]]
    verify: t.Callable[[Ctx, DAL, dict[str, t.Any], t.Any], None]
    mutating: bool = False
    max_n: dict[str, int] | None = None
    sizes: tuple[int, ...] | None = None  # overrides the profile's sizes
    extra: dict[str, t.Any] = field(default_factory=dict)


class VerificationError(AssertionError):
    pass


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise VerificationError(msg)


# --- checksums -------------------------------------------------------------------------------


def _post_sums(items: t.Iterable[tuple[int, dict[str, t.Any]]]) -> tuple[t.Any, ...]:
    count = ids = views = titles = bodies = published = authors = seconds = 0
    score = 0.0
    for post_id, p in items:
        count += 1
        ids += post_id
        views += p["views"]
        titles += len(p["title"])
        bodies += len(p["body"])
        published += 1 if p["published"] else 0
        authors += int(p["author"])
        seconds += int((p["created_at"] - schema.BASE_TIME).total_seconds())
        score += p["score"]
    return (count, ids, views, titles, bodies, published, authors, seconds, score)


def expected_full(posts: list[dict[str, t.Any]], only_published: bool = False) -> tuple[t.Any, ...]:
    return _post_sums((i + 1, p) for i, p in enumerate(posts) if not only_published or p["published"])


def consume_full(rows: t.Iterable[t.Any]) -> tuple[t.Any, ...]:
    """Read every column of every post row/instance."""
    return _post_sums(
        (
            r.id,
            {
                "views": r.views,
                "title": r.title,
                "body": r.body,
                "published": r.published,
                "author": r.author,
                "created_at": r.created_at,
                "score": r.score,
            },
        )
        for r in rows
    )


def same_sums(got: tuple[t.Any, ...], want: tuple[t.Any, ...]) -> bool:
    return got[:-1] == want[:-1] and math.isclose(got[-1], want[-1], rel_tol=1e-9, abs_tol=1e-6)


def table_stats(db: DAL) -> tuple[int, int, int, int]:
    """(count, sum(id), sum(views), published count) straight from SQL."""
    row = db.executesql(
        'SELECT COUNT(*), COALESCE(SUM("id"), 0), COALESCE(SUM("views"), 0), '
        'COALESCE(SUM(CASE WHEN "published" = \'T\' THEN 1 ELSE 0 END), 0) FROM "bench_post";'
    )[0]
    return tuple(int(v) for v in row)  # type: ignore[return-value]


# --- prepares --------------------------------------------------------------------------------


def prep_seed(ctx: Ctx, db: DAL, n: int) -> dict[str, t.Any]:
    authors, posts = schema.seed(db, n)
    return {"n": n, "authors": authors, "posts": posts, "db": db}


def prep_seed_double(ctx: Ctx, db: DAL, n: int) -> dict[str, t.Any]:
    """2n posts of which exactly n are published."""
    authors, posts = schema.seed(db, 2 * n)
    return {"n": n, "authors": authors, "posts": posts, "db": db}


def prep_insert(ctx: Ctx, db: DAL, n: int) -> dict[str, t.Any]:
    """Authors only; the timed part inserts the posts."""
    n_authors = schema.n_authors_for(n)
    schema.seed(db, 0, n_authors=n_authors)
    return {"n": n, "posts": schema.post_rows(n, n_authors), "db": db}


# --- reads -----------------------------------------------------------------------------------


def verify_full(ctx: Ctx, db: DAL, s: dict[str, t.Any], got: t.Any) -> None:
    want = expected_full(s["posts"])
    check(same_sums(got, want), f"full read checksum mismatch: {got} != {want}")


def verify_filtered(ctx: Ctx, db: DAL, s: dict[str, t.Any], got: t.Any) -> None:
    want = expected_full(s["posts"], only_published=True)
    check(got[0] == s["n"], f"expected {s['n']} rows, got {got[0]}")
    check(same_sums(got, want), f"filtered read checksum mismatch: {got} != {want}")


def _columns_sums(rows: t.Iterable[t.Any]) -> tuple[int, int, int, int]:
    count = ids = titles = views = 0
    for r in rows:
        count += 1
        ids += r.id
        titles += len(r.title)
        views += r.views
    return (count, ids, titles, views)


def verify_columns(ctx: Ctx, db: DAL, s: dict[str, t.Any], got: t.Any) -> None:
    posts = s["posts"]
    want = (
        len(posts),
        len(posts) * (len(posts) + 1) // 2,
        sum(len(p["title"]) for p in posts),
        sum(p["views"] for p in posts),
    )
    check(got == want, f"column read checksum mismatch: {got} != {want}")


def _by_id_sums(rows: t.Iterable[t.Any]) -> tuple[int, int, int]:
    count = ids = views = 0
    for r in rows:
        count += 1
        ids += r.id
        views += r.views
    return (count, ids, views)


def verify_by_id(ctx: Ctx, db: DAL, s: dict[str, t.Any], got: t.Any) -> None:
    posts = s["posts"]
    want = (len(posts), len(posts) * (len(posts) + 1) // 2, sum(p["views"] for p in posts))
    check(got == want, f"lookup checksum mismatch: {got} != {want}")


def _join_expected(s: dict[str, t.Any]) -> tuple[int, int, int]:
    posts, authors = s["posts"], s["authors"]
    return (
        len(posts),
        len(posts) * (len(posts) + 1) // 2,
        sum(len(authors[int(p["author"]) - 1]["name"]) for p in posts),
    )


def verify_join(ctx: Ctx, db: DAL, s: dict[str, t.Any], got: t.Any) -> None:
    want = _join_expected(s)
    check(got == want, f"join checksum mismatch: {got} != {want}")


def verify_join_many(ctx: Ctx, db: DAL, s: dict[str, t.Any], got: t.Any) -> None:
    want = _join_expected(s)
    check(got[:3] == want, f"one-to-many checksum mismatch: {got[:3]} != {want}")
    if got[3] is not None:
        check(got[3] == len(s["authors"]), f"expected {len(s['authors'])} authors, got {got[3]}")


def join_many_pydal(ctx: Ctx, s: dict[str, t.Any]) -> t.Any:
    db = ctx.pdb
    rows = db(db.bench_author).select(
        db.bench_author.ALL, db.bench_post.ALL, left=db.bench_post.on(db.bench_author.id == db.bench_post.author)
    )
    count = ids = names = 0
    for r in rows:
        count += 1
        ids += r.bench_post.id
        names += len(r.bench_author.name)
    return (count, ids, names, None)  # flat rows: no per-author grouping in pydal


def join_many_typedal(ctx: Ctx, s: dict[str, t.Any]) -> t.Any:
    count = ids = names = n_authors = 0
    for author in ctx.Author.join("posts").collect():
        n_authors += 1
        for post in author.posts:
            count += 1
            ids += post.id
            names += len(author.name)
    return (count, ids, names, n_authors)


def join_one_pydal(ctx: Ctx, s: dict[str, t.Any]) -> t.Any:
    db = ctx.pdb
    rows = db(db.bench_post).select(
        db.bench_post.ALL, db.bench_author.ALL, left=db.bench_author.on(db.bench_post.author == db.bench_author.id)
    )
    count = ids = names = 0
    for r in rows:
        count += 1
        ids += r.bench_post.id
        names += len(r.bench_author.name)
    return (count, ids, names)


def join_one_typedal(ctx: Ctx, s: dict[str, t.Any]) -> t.Any:
    count = ids = names = 0
    for post in ctx.Post.join("author").collect():
        count += 1
        ids += post.id
        names += len(post.author.name)
    return (count, ids, names)


def prep_hydrate(ctx: Ctx, db: DAL, n: int) -> dict[str, t.Any]:
    s = prep_seed(ctx, db, n)
    s["raw"] = ctx.Post.select().execute()
    return s


# --- writes ----------------------------------------------------------------------------------


def verify_inserted(ctx: Ctx, db: DAL, s: dict[str, t.Any], got: t.Any) -> None:
    n, posts = s["n"], s["posts"]
    stats = table_stats(db)
    want = (n, n * (n + 1) // 2, sum(p["views"] for p in posts), sum(1 for p in posts if p["published"]))
    check(stats == want, f"insert left {stats}, expected {want}")
    check(len(got) == n, f"insert returned {len(got)} results, expected {n}")


def verify_validated(ctx: Ctx, db: DAL, s: dict[str, t.Any], got: t.Any) -> None:
    verify_inserted(ctx, db, s, got)
    for item in got:
        errors = item[1] if isinstance(item, tuple) else item.get("errors")
        check(not errors, f"validation errors on valid data: {errors}")


def insert_loop(insert: t.Callable[..., t.Any], db: DAL, posts: list[dict[str, t.Any]]) -> list[t.Any]:
    out = [insert(**p) for p in posts]
    db.commit()
    return out


def verify_updated(ctx: Ctx, db: DAL, s: dict[str, t.Any], got: t.Any) -> None:
    n, posts = s["n"], s["posts"]
    stats = table_stats(db)
    want = (len(posts), len(posts) * (len(posts) + 1) // 2, sum(p["views"] for p in posts) + n, stats[3])
    check(stats == want, f"update left {stats}, expected {want}")
    updated = got if isinstance(got, int) else len(got)
    check(updated == n, f"update reported {updated} rows, expected {n}")


def prep_update_records(ctx: Ctx, db: DAL, n: int) -> dict[str, t.Any]:
    s = prep_seed(ctx, db, n)
    s["records"] = list(db(db.bench_post).select()) if db is ctx.pdb else list(ctx.Post.collect())
    db.commit()
    return s


def update_records(db: DAL, records: list[t.Any]) -> int:
    for r in records:
        r.update_record(views=r.views + 1)
    db.commit()
    return len(records)


def verify_deleted(ctx: Ctx, db: DAL, s: dict[str, t.Any], got: t.Any) -> None:
    n, posts = s["n"], s["posts"]
    stats = table_stats(db)
    left = [(i + 1, p) for i, p in enumerate(posts) if not p["published"]]
    want = (n, sum(i for i, _ in left), sum(p["views"] for _, p in left), 0)
    check(stats == want, f"delete left {stats}, expected {want}")
    deleted = got if isinstance(got, int) else len(got)
    check(deleted == n, f"delete reported {deleted} rows, expected {n}")


# --- cold path -------------------------------------------------------------------------------


def prep_nothing(ctx: Ctx, db: DAL, n: int) -> dict[str, t.Any]:
    return {"n": n, "models": schema.make_models()}


def connect_define_pydal(ctx: Ctx, s: dict[str, t.Any]) -> t.Any:
    db = schema.new_pydal(ctx.uris["pydal"], ctx.folders["pydal"])
    schema.define_pydal(db)
    return db


def connect_define_typedal(ctx: Ctx, s: dict[str, t.Any]) -> t.Any:
    db = schema.new_typedal(ctx.uris["typedal"], ctx.folders["typedal"])
    author, post = s["models"]
    db.define(author)
    db.define(post)
    return db


def verify_connected(ctx: Ctx, db: DAL, s: dict[str, t.Any], got: t.Any) -> None:
    check("bench_post" in got.tables and "bench_author" in got.tables, "tables not defined")
    got.close()


# --- registry --------------------------------------------------------------------------------


def _commit(db: DAL, value: t.Any) -> t.Any:
    db.commit()
    return value


CASES: list[Case] = [
    Case(
        "select_full",
        "read",
        "Read all N rows with all columns",
        prep_seed,
        {
            "pydal": lambda c, s: consume_full(c.pdb(c.pdb.bench_post).select()),
            "mixed": lambda c, s: consume_full(c.tdb(c.tdb.bench_post).select()),
            "typedal_execute": lambda c, s: consume_full(c.Post.select().execute()),
            "typedal": lambda c, s: consume_full(c.Post.collect()),
        },
        verify_full,
    ),
    Case(
        "select_columns",
        "read",
        "Read all N rows, selecting 3 columns (id, title, views)",
        prep_seed,
        {
            "pydal": lambda c, s: _columns_sums(
                c.pdb(c.pdb.bench_post).select(c.pdb.bench_post.id, c.pdb.bench_post.title, c.pdb.bench_post.views)
            ),
            "typedal": lambda c, s: _columns_sums(c.Post.select(c.Post.id, c.Post.title, c.Post.views).collect()),
        },
        verify_columns,
    ),
    Case(
        "select_filtered",
        "read",
        "Table holds 2N rows; WHERE published = true returns N",
        prep_seed_double,
        {
            "pydal": lambda c, s: consume_full(c.pdb(c.pdb.bench_post.published == True).select()),  # noqa: E712
            "mixed": lambda c, s: consume_full(c.Post.where(c.tdb.bench_post.published == True).collect()),  # noqa: E712
            "typedal": lambda c, s: consume_full(c.Post.where(c.Post.published == True).collect()),  # noqa: E712
        },
        verify_filtered,
    ),
    Case(
        "select_by_id",
        "read",
        "N single-row lookups by primary key",
        prep_seed,
        {
            "pydal": lambda c, s: _by_id_sums(c.pdb.bench_post(i) for i in range(1, s["n"] + 1)),
            "typedal": lambda c, s: _by_id_sums(c.Post(i) for i in range(1, s["n"] + 1)),
        },
        verify_by_id,
        max_n=LOOP_CAP,
    ),
    Case(
        "join_many_to_one",
        "join",
        "N posts, each with its author (left join)",
        prep_seed,
        {"pydal": join_one_pydal, "typedal": join_one_typedal},
        verify_join,
    ),
    Case(
        "join_one_to_many",
        "join",
        "N/10 authors, each with its posts (N posts in total; left join)",
        prep_seed,
        {"pydal": join_many_pydal, "typedal": join_many_typedal},
        verify_join_many,
    ),
    Case(
        "hydrate",
        "hydrate",
        "Turn N already-fetched pydal rows into model instances (TypedRows.from_rows), no query",
        prep_hydrate,
        {"typedal": lambda c, s: consume_full(TypedRows.from_rows(s["raw"], c.Post))},
        verify_full,
    ),
    Case(
        "insert_bulk",
        "insert",
        "Insert N rows with one bulk_insert call, then commit",
        prep_insert,
        {
            "pydal": lambda c, s: _commit(c.pdb, c.pdb.bench_post.bulk_insert(s["posts"])),
            "mixed": lambda c, s: _commit(c.tdb, c.tdb.bench_post.bulk_insert(s["posts"])),
            "typedal": lambda c, s: _commit(c.tdb, c.Post.bulk_insert(s["posts"])),
        },
        verify_inserted,
        mutating=True,
        max_n=LOOP_CAP,
    ),
    Case(
        "insert_loop",
        "insert",
        "Insert N rows one insert() at a time, then commit",
        prep_insert,
        {
            "pydal": lambda c, s: insert_loop(c.pdb.bench_post.insert, c.pdb, s["posts"]),
            "typedal": lambda c, s: insert_loop(c.Post.insert, c.tdb, s["posts"]),
        },
        verify_inserted,
        mutating=True,
        max_n=LOOP_CAP,
    ),
    Case(
        "insert_validated",
        "insert",
        "Insert N rows one validate_and_insert() at a time (default validators), then commit",
        prep_insert,
        {
            "pydal": lambda c, s: insert_loop(c.pdb.bench_post.validate_and_insert, c.pdb, s["posts"]),
            "typedal": lambda c, s: insert_loop(c.Post.validate_and_insert, c.tdb, s["posts"]),
        },
        verify_validated,
        mutating=True,
        max_n=LOOP_CAP,
    ),
    Case(
        "update_query",
        "update",
        "Table holds 2N rows; one UPDATE (views = views + 1) WHERE published hits N, then commit",
        prep_seed_double,
        {
            "pydal": lambda c, s: _commit(
                c.pdb,
                c.pdb(c.pdb.bench_post.published == True).update(views=c.pdb.bench_post.views + 1),  # noqa: E712
            ),
            "mixed": lambda c, s: _commit(
                c.tdb,
                c.tdb(c.tdb.bench_post.published == True).update(views=c.tdb.bench_post.views + 1),  # noqa: E712
            ),
            "typedal": lambda c, s: _commit(
                c.tdb,
                c.Post.where(c.Post.published == True).update(views=c.Post.views + 1),  # noqa: E712
            ),
        },
        verify_updated,
        mutating=True,
    ),
    Case(
        "update_records",
        "update",
        "update_record() on N already-fetched rows/instances, then commit",
        prep_update_records,
        {
            "pydal": lambda c, s: update_records(c.pdb, s["records"]),
            "typedal": lambda c, s: update_records(c.tdb, s["records"]),
        },
        verify_updated,
        mutating=True,
        max_n=LOOP_CAP,
    ),
    Case(
        "delete_query",
        "delete",
        "Table holds 2N rows; one DELETE WHERE published removes N, then commit",
        prep_seed_double,
        {
            "pydal": lambda c, s: _commit(c.pdb, c.pdb(c.pdb.bench_post.published == True).delete()),  # noqa: E712
            "mixed": lambda c, s: _commit(c.tdb, c.tdb(c.tdb.bench_post.published == True).delete()),  # noqa: E712
            "typedal": lambda c, s: _commit(c.tdb, c.Post.where(c.Post.published == True).delete()),  # noqa: E712
        },
        verify_deleted,
        mutating=True,
    ),
    Case(
        "connect_and_define",
        "schema",
        "Cold path: new DB instance on an existing database, define both tables (migration check)",
        prep_nothing,
        {"pydal": connect_define_pydal, "typedal": connect_define_typedal},
        verify_connected,
        mutating=True,
        sizes=(1,),
    ),
]

CASES_BY_NAME = {c.name: c for c in CASES}
