"""Benchmark schema, deterministic data generation and fast (untimed) seeding."""

import datetime as dt
import random
import typing as t

from pydal import DAL, Field

from typedal import TypeDAL, TypedTable, relationship
from typedal.fields import TextField

SEED = 42
BASE_TIME = dt.datetime(2024, 1, 1)
BODY = " ".join(["lorem ipsum dolor sit amet"] * 8)


def make_models() -> tuple[type[TypedTable], type[TypedTable]]:
    """Fresh model classes, so every TypeDAL instance gets its own (cold-path benchmarks redefine them)."""

    class BenchAuthor(TypedTable):
        name: str
        email: str
        posts = relationship(list["BenchPost"], condition=lambda author, post: author.id == post.author, join="left")

    class BenchPost(TypedTable):
        title: str
        body = TextField()
        views: int
        score: float
        published: bool
        created_at: dt.datetime
        author: BenchAuthor

    return BenchAuthor, BenchPost


def define_pydal(db: DAL) -> None:
    """The same schema in plain pydal (same column types and order as the TypeDAL models)."""
    db.define_table("bench_author", Field("name"), Field("email"))
    db.define_table(
        "bench_post",
        Field("title"),
        Field("views", "integer"),
        Field("score", "double"),
        Field("published", "boolean"),
        Field("created_at", "datetime"),
        Field("author", "reference bench_author"),
        Field("body", "text"),
    )


def n_authors_for(n_posts: int) -> int:
    """Ten posts per author."""
    return max(1, n_posts // 10)


def author_rows(n: int) -> list[dict[str, t.Any]]:
    """Deterministic author payloads."""
    return [{"name": f"author-{i}", "email": f"author-{i}@example.com"} for i in range(n)]


def post_rows(n: int, n_authors: int, seed: int = SEED) -> list[dict[str, t.Any]]:
    """Deterministic post payloads; even indexes are published, author ids cycle over 1..n_authors."""
    rng = random.Random(seed)
    return [
        {
            "title": f"post-{i}",
            "body": f"{BODY} {i}",
            "views": rng.randint(0, 10_000),
            "score": round(rng.random() * 100, 4),
            "published": i % 2 == 0,
            "created_at": BASE_TIME + dt.timedelta(minutes=i),
            "author": i % n_authors + 1,
        }
        for i in range(n)
    ]


def reset(db: DAL) -> None:
    """Empty both tables and restart their id sequences, so every seed yields ids 1..n."""
    if db._dbname == "postgres":
        db.executesql('TRUNCATE "bench_post", "bench_author" RESTART IDENTITY CASCADE;')
    else:
        db.executesql('DELETE FROM "bench_post";')
        db.executesql('DELETE FROM "bench_author";')
        db.executesql("DELETE FROM sqlite_sequence WHERE name IN ('bench_post', 'bench_author');")
    db.commit()


_SQL_CACHE: dict[tuple[str, str, int, int], list[str]] = {}


def _raw_insert(db: DAL, tablename: str, rows: list[dict[str, t.Any]], batch: int = 500) -> None:
    """Multi-row INSERT using pydal's own value encoding, bypassing the ORM (seeding only, never timed)."""
    if not rows:
        return
    key = (db._dbname, tablename, len(rows), int(rows[-1].get("author", 0)))
    if key not in _SQL_CACHE:
        _SQL_CACHE[key] = list(_insert_statements(db, tablename, rows, batch))
    for sql in _SQL_CACHE[key]:
        db.executesql(sql)


def _insert_statements(db: DAL, tablename: str, rows: list[dict[str, t.Any]], batch: int) -> t.Iterator[str]:
    table = db[tablename]
    names = list(rows[0])
    fields = [table[name] for name in names]
    cols = ", ".join(f._rname for f in fields)
    represent = db._adapter.represent
    for start in range(0, len(rows), batch):
        chunk = rows[start : start + batch]
        values = ", ".join("(" + ", ".join(str(represent(row[f.name], f.type)) for f in fields) + ")" for row in chunk)
        yield f"INSERT INTO {table._rname} ({cols}) VALUES {values};"


def seed(db: DAL, n_posts: int, n_authors: int | None = None) -> tuple[list[dict[str, t.Any]], list[dict[str, t.Any]]]:
    """Reset and fill with n_posts posts (and their authors); returns the payloads used."""
    reset(db)
    n_authors = n_authors or n_authors_for(n_posts)
    authors = author_rows(n_authors)
    posts = post_rows(n_posts, n_authors)
    _raw_insert(db, "bench_author", authors)
    _raw_insert(db, "bench_post", posts)
    db.commit()
    return authors, posts


def new_typedal(uri: str, folder: str) -> TypeDAL:
    """TypeDAL with library defaults, ignoring any pyproject/env config of the host checkout."""
    return TypeDAL(uri, folder=folder, migrate=True, use_pyproject=False, use_env=False, attempts=1)


def new_pydal(uri: str, folder: str) -> DAL:
    """Plain pydal with library defaults."""
    return DAL(uri, folder=folder, migrate=True, attempts=1)
