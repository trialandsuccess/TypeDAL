"""
Cached queries must return what the database holds after every kind of write, on SQLite and PostgreSQL.

Pins the cache invalidation behaviour before the dependency probe (performance step 1) changes how it runs.
"""

import select
import subprocess
import sys
import typing as t
from pathlib import Path

import pytest

from src.typedal import TypeDAL, TypedField, TypedTable, relationship
from src.typedal.caching import cache_models

from .compat_support import compat_uri, drop_all, is_postgres, make_db

__all__ = ["compat_uri"]


# module level, because the cache stores rows with `dill`:
class CachePet(TypedTable):
    name: str
    code = TypedField(str, unique=True, notnull=False)
    owner: int | None
    tenant: int | None


class CacheOwner(TypedTable):
    name: str
    pets = relationship(list["CachePet"], lambda self, other: other.owner == self.id)


class CacheOther(TypedTable):
    name: str


CACHE_MODELS = (CacheOwner, CachePet, CacheOther)


@pytest.fixture
def cdb(compat_uri: str, tmp_path: Path) -> t.Iterator[TypeDAL]:
    # a cache hit sets instance methods on the model class (pre-existing), so undo that after each test:
    snapshot = {model: set(model.__dict__) for model in CACHE_MODELS}
    db = make_db(compat_uri, tmp_path, enable_typedal_caching=True)
    try:
        db.define(CacheOwner)
        db.define(CachePet)
        db.define(CacheOther)
        for i in range(70):  # more rows than one id IN (...) batch would hold
            CachePet.insert(name=f"p{i:02d}", code=f"c{i:02d}", owner=1 + i % 2, tenant=i % 2)
        CacheOwner.insert(name="o1")
        CacheOwner.insert(name="o2")
        db.commit()
        yield db
    finally:
        drop_all(db)
        for model, keys in snapshot.items():
            for name in set(model.__dict__) - keys:
                type.__delattr__(model, name)


def cached_names() -> list[str]:
    return sorted(p.name for p in CachePet.where(CachePet.id > 0).cache().collect())


def cached_first() -> str | None:
    first = CachePet.where(CachePet.name.like("p0%")).orderby(CachePet.id).cache().first()
    return first.name if first else None


def cached_owner_pets() -> list[tuple[str, list[str]]]:
    owners = CacheOwner.join("pets").orderby(CacheOwner.id).cache().collect()
    return [(o.name, sorted(p.name for p in o.pets)) for o in owners]


def hit_statuses() -> list[str | None]:
    """Cache status of the collect() queries warm() runs: "cached" on a hit."""
    names = CachePet.where(CachePet.id > 0).cache().collect()
    owners = CacheOwner.join("pets").orderby(CacheOwner.id).cache().collect()
    return [names.metadata["cache"]["status"], owners.metadata["cache"]["status"]]


def truth(db: TypeDAL) -> tuple[list[str], str | None, list[tuple[str, list[str]]]]:
    names = sorted(r.name for r in db(db.cache_pet).select(db.cache_pet.name))
    first = db(db.cache_pet.name.like("p0%")).select(db.cache_pet.name, orderby=db.cache_pet.id, limitby=(0, 1))
    owners = [
        (o.name, sorted(p.name for p in db(db.cache_pet.owner == o.id).select(db.cache_pet.name)))
        for o in db(db.cache_owner).select(orderby=db.cache_owner.id)
    ]
    return names, (first[0].name if first else None), owners


def warm() -> tuple[list[str], str | None, list[tuple[str, list[str]]]]:
    return cached_names(), cached_first(), cached_owner_pets()


def pet(db: TypeDAL, name: str) -> CachePet:
    return t.cast(CachePet, CachePet.where(name=name).first())


WRITES: dict[str, t.Callable[[TypeDAL], t.Any]] = {
    "pydal_insert": lambda db: db.cache_pet.insert(name="a1", owner=1),
    "pydal_bulk_insert": lambda db: db.cache_pet.bulk_insert([{"name": "b1"}, {"name": "b2", "owner": 2}]),
    "model_insert": lambda db: CachePet.insert(name="c1", owner=2),
    "model_bulk_insert": lambda db: CachePet.bulk_insert([{"name": "d1", "owner": 1}, {"name": "d2"}]),
    "model_bulk_insert_empty": lambda db: CachePet.bulk_insert([]),
    "validate_and_insert": lambda db: CachePet.validate_and_insert(name="e1"),
    "upsert_insert": lambda db: CachePet.upsert({"code": "new"}, name="u1"),
    "upsert_update": lambda db: CachePet.upsert({"code": "c03"}, name="u2"),
    "update_or_insert_insert": lambda db: CachePet.update_or_insert(CachePet.name == "nope", name="ui1"),
    "update_or_insert_update": lambda db: CachePet.update_or_insert(CachePet.name == "p04", name="ui2"),
    "pydal_update_one": lambda db: db(db.cache_pet.name == "p01").update(name="q1"),
    "pydal_update_all": lambda db: db(db.cache_pet.id > 0).update(name=db.cache_pet.name + "x"),
    "pydal_setitem": lambda db: db.cache_pet.__setitem__(2, dict(name="q2")),
    "builder_update": lambda db: CachePet.where(CachePet.name.like("p1%")).update(name="q3"),
    "rows_update": lambda db: CachePet.where(owner=1).collect().update(name="q4"),
    "update_record": lambda db: pet(db, "p06").update_record(name="q6"),
    "setattr_then_update_record": lambda db: _setattr_update(pet(db, "p07")),
    "validate_and_update": lambda db: CachePet.validate_and_update(CachePet.name == "p08", name="q8"),
    "move_owner": lambda db: CachePet.where(name="p09").update(owner=1),
    "pydal_delete_one": lambda db: db(db.cache_pet.name == "p10").delete(),
    "pydal_delete_many": lambda db: db(db.cache_pet.name.like("p%")).delete(),
    "pydal_delitem": lambda db: db.cache_pet.__delitem__(3),
    "builder_delete": lambda db: CachePet.where(CachePet.name.like("p2%")).delete(),
    "builder_delete_nothing": lambda db: CachePet.where(name="nope").delete(),
    "rows_delete": lambda db: CachePet.where(owner=2).collect().delete(),
    "delete_record": lambda db: pet(db, "p11").delete_record(),
    "delete_owner": lambda db: CacheOwner.where(name="o2").delete(),
    "truncate": lambda db: db.cache_pet.truncate(),
    "other_table_insert": lambda db: CacheOther.insert(name="o"),
    "other_table_bulk": lambda db: CacheOther.bulk_insert([{"name": "o2"}]),
}


def _setattr_update(p: CachePet) -> None:
    p.name = "q7"
    p.update_record()


NO_CHANGE = {"model_bulk_insert_empty", "builder_delete_nothing", "other_table_insert", "other_table_bulk"}


@pytest.mark.parametrize("write", sorted(WRITES))
def test_cached_queries_follow_writes(cdb: TypeDAL, write: str) -> None:
    assert warm() == truth(cdb)
    assert warm() == truth(cdb)
    assert hit_statuses() == ["cached", "cached"]
    before = truth(cdb)

    WRITES[write](cdb)

    # the write really happened (or really changed nothing):
    assert (truth(cdb) == before) == (write in NO_CHANGE)
    if write == "truncate":
        # truncate runs no hooks, so the cache keeps its rows until it is cleared (pinned as is)
        assert cached_names() != truth(cdb)[0]
        return
    assert warm() == truth(cdb)


def test_hook_that_reads_the_cache_during_bulk_insert(cdb: TypeDAL) -> None:
    seen: list[int] = []
    CachePet.after_insert(lambda row, rid: seen.append(len(cached_names())))
    try:
        warm()
        CachePet.bulk_insert([{"name": "h1"}, {"name": "h2"}, {"name": "h3"}])
        cdb.cache_pet.bulk_insert([{"name": "h4"}, {"name": "h5"}])
    finally:
        CachePet._after_insert.pop()

    # PyDAL inserts every row before it runs the after_insert hooks, and the cache is right afterwards:
    assert seen == [73, 73, 73, 75, 75]
    assert warm() == truth(cdb)


def test_common_filter_on_the_written_table(cdb: TypeDAL) -> None:
    warm()
    cdb.cache_pet._common_filter = lambda query: cdb.cache_pet.tenant == 1
    try:
        CachePet.where(CachePet.name.like("p%")).delete()
        cdb(cdb.cache_pet.name.like("p%")).update(name="hidden")
        CachePet.insert(name="t1", tenant=1)
    finally:
        cdb.cache_pet._common_filter = None

    # only tenant 1 rows were touched; the cache must see exactly that:
    assert warm() == truth(cdb)
    assert sum(n.startswith("p") for n in cached_names()) == 35


def test_common_filter_limits_what_a_delete_invalidates(cdb: TypeDAL) -> None:
    def tenant_names(tenant: int) -> tuple[list[str], str | None]:
        rows = CachePet.where(tenant=tenant).cache().collect()
        return sorted(p.name for p in rows), rows.metadata["cache"]["status"]

    tenant_names(0)
    tenant_names(1)
    cdb.cache_pet._common_filter = lambda query: cdb.cache_pet.tenant == 1
    try:
        cdb(cdb.cache_pet.name.like("p%")).delete()  # a raw Set: only the cache hook picks the ids
    finally:
        cdb.cache_pet._common_filter = None

    zero, zero_status = tenant_names(0)
    one, one_status = tenant_names(1)
    assert (len(zero), zero_status) == (35, "cached")  # tenant 0 rows were hidden, so their entry survives
    assert (one, one_status) == ([], "fresh")


def test_common_filter_on_the_dependency_table(cdb: TypeDAL) -> None:
    # cache_other has no writes yet, so whatever a write learns about its dependencies is learnt under the filter:
    def cached_others() -> list[str]:
        return sorted(o.name for o in CacheOther.where(CacheOther.id > 0).cache().collect())

    cdb.executesql("INSERT INTO cache_other (name) VALUES ('h0');")  # raw SQL runs no hooks
    _, dependency = cache_models(cdb)
    dependency_table = dependency._table
    dependency_table._common_filter = lambda query: dependency_table.idx < 0  # hides every dependency
    try:
        cached_others()
        CacheOther.insert(name="h1")
        assert cached_others() == ["h0"]  # the hook could not see the dependency, so the entry is stale (pinned as is)
    finally:
        dependency_table._common_filter = None

    # the filter is gone, so the next write must see the dependency as it is now:
    CacheOther.insert(name="h2")
    assert cached_others() == ["h0", "h1", "h2"]


def test_rollback_undoes_writes_and_their_cache_changes(cdb: TypeDAL) -> None:
    before = warm()
    cdb.commit()
    CachePet.insert(name="r1")
    CachePet.where(name="p00").delete()
    assert warm() == truth(cdb)
    cdb.rollback()

    assert warm() == truth(cdb) == before


def test_cache_entries_are_removed_by_writes(cdb: TypeDAL) -> None:
    cache, dependency = cache_models(cdb)
    warm()
    assert cache.count() == 3

    CacheOther.insert(name="unrelated")
    assert cache.count() == 3  # nothing cached depends on cache_other

    CachePet.where(name="p01").update(name="z")
    assert cache.count() == 1  # only the "first p0%" query (which returned p00) survives

    CachePet.insert(name="new")
    assert cache.count() == 0
    assert dependency.count() == 0


WRITER = """
import sys
from src.typedal import TypeDAL
from tests.test_compat_cache import CacheOwner, CachePet
db = TypeDAL(sys.argv[1], folder=sys.argv[2], migrate=False, use_pyproject=False, use_env=False, attempts=1)
db.define(CacheOwner)
db.define(CachePet)
for line in sys.stdin:
    cmd, *args = line.split()
    if cmd == "insert":
        CachePet.insert(name=args[0])
    elif cmd == "delete":
        CachePet.where(name=args[0]).delete()
    elif cmd == "update":
        CachePet.where(name=args[0]).update(name=args[1])
    db.commit()
    print("ok", flush=True)
db.close()
"""


def test_writes_on_another_connection_invalidate(compat_uri: str, tmp_path: Path, cdb: TypeDAL) -> None:
    if not is_postgres(cdb):
        pytest.skip("needs a second connection to the same database")
    other_folder = tmp_path / "other"
    other_folder.mkdir()
    root = Path(__file__).parent.parent
    writer = subprocess.Popen(
        [sys.executable, "-c", WRITER, compat_uri, str(other_folder)],
        cwd=root,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert writer.stdin and writer.stdout

    def send(command: str) -> None:
        writer.stdin.write(command + "\n")
        writer.stdin.flush()
        ready, _, _ = select.select([writer.stdout], [], [], 60)
        if not ready:
            writer.kill()
            pytest.fail(f"writer stalled on {command!r}")
        assert writer.stdout.readline().strip() == "ok"

    try:
        # the writer first writes while nothing is cached, so it must not remember "nothing to invalidate":
        send("insert x0")
        cdb.commit()
        for command in ("delete p00", "update p01 x2", "insert x1"):
            warm()
            assert hit_statuses() == ["cached", "cached"]
            cdb.commit()  # the writer can see the cache entries now
            before = truth(cdb)

            send(command)

            cdb.commit()  # start a new transaction, so this connection sees the writer's commit
            assert truth(cdb) != before
            assert warm() == truth(cdb), command
    finally:
        writer.stdin.close()
        try:
            writer.wait(timeout=60)
        except subprocess.TimeoutExpired:
            writer.kill()
            writer.wait()
    assert writer.returncode == 0


@pytest.mark.xfail(
    strict=True, raises=AttributeError, reason="pre-existing: a cache hit sets instance methods on the model class"
)
def test_classmethod_update_after_a_cache_hit(cdb: TypeDAL) -> None:
    assert CachePet.update(CachePet.name == "p04", name="q4") is not None  # works before any cache hit
    warm()
    assert hit_statuses() == ["cached", "cached"]  # the hit replaces CachePet.update with the instance variant

    CachePet.update(CachePet.name == "p05", name="q5")
    assert warm() == truth(cdb)
