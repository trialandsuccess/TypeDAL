"""
The cache dependency probe: writes skip invalidation when nothing cached depends on their table.
"""

import select
import subprocess
import sys
import time
import typing as t
from pathlib import Path

import pytest
from pydal.helpers.classes import ExecutionHandler

from src.typedal import TypeDAL, TypedTable
from src.typedal.caching import (
    AfterInsertCacheHook,
    AfterUpdateCacheHook,
    BeforeDeleteCacheHook,
    cache_models,
    has_cache_dependencies,
)

from .compat_support import compat_uri, drop_all, is_postgres, make_db

__all__ = ["compat_uri"]


class ProbePet(TypedTable):
    name: str


class Recorder(ExecutionHandler):
    """Records every statement that passes through PyDAL's execution handlers."""

    def before_execute(self, command: str) -> None:
        getattr(self.adapter.db, "statements", []).append(command)


class RecordingDAL(TypeDAL):
    execution_handlers = [*TypeDAL.execution_handlers, Recorder]


@pytest.fixture
def pdb(compat_uri: str, tmp_path: Path) -> t.Iterator[t.Any]:
    db = make_db(compat_uri, tmp_path, cls=RecordingDAL, enable_typedal_caching=True)
    try:
        db.define(ProbePet)
        for name in ("a", "b", "c"):
            ProbePet.insert(name=name)
        db.commit()
        db.statements = []
        yield db
    finally:
        drop_all(db)


def cache_statements(db: t.Any) -> list[str]:
    """Statements that read or change the cache tables, other than the probe."""
    return [s for s in db.statements if "typedal_cache" in s and "LIMIT 1" not in s]


def names() -> list[str]:
    return sorted(p.name for p in ProbePet.where(ProbePet.id > 0).cache().collect())


def test_hooks_are_objects(pdb: t.Any) -> None:
    table = pdb.probe_pet
    assert [type(h) for h in table._after_insert] == [AfterInsertCacheHook]
    assert [type(h) for h in table._after_update] == [AfterUpdateCacheHook]
    assert [type(h) for h in table._before_delete if hasattr(h, "tablename")] == [BeforeDeleteCacheHook]
    assert table._after_delete == []
    assert table._after_insert[0].tablename == "probe_pet"
    assert table._after_insert[0].db is pdb


def test_writes_without_cache_entries_only_probe(pdb: t.Any) -> None:
    assert not has_cache_dependencies(pdb, "probe_pet")

    ProbePet.insert(name="d")
    ProbePet.bulk_insert([{"name": "e"}, {"name": "f"}])
    ProbePet.where(name="a").update(name="a2")
    ProbePet.where(name="b").delete()
    pdb(pdb.probe_pet.name == "c").delete()

    assert cache_statements(pdb) == []
    probes = [s for s in pdb.statements if "typedal_cache_dependency" in s]
    assert (
        len(probes) == 6
    )  # the check above, then one per insert call (the bulk insert probes once), 1 update, 2 deletes
    assert sorted(r.name for r in pdb(pdb.probe_pet).select()) == ["a2", "d", "e", "f"]


def test_writes_with_cache_entries_invalidate(pdb: t.Any) -> None:
    assert names() == ["a", "b", "c"]
    assert has_cache_dependencies(pdb, "probe_pet")
    assert not has_cache_dependencies(pdb, "other_table")

    ProbePet.insert(name="d")
    assert cache_statements(pdb)
    assert not has_cache_dependencies(pdb, "probe_pet")
    assert names() == ["a", "b", "c", "d"]


def test_probe_sql_is_reused_per_table(pdb: t.Any) -> None:
    has_cache_dependencies(pdb, "probe_pet")
    probes = pdb.__dict__["_typedal_dependency_probes"]
    first = probes["probe_pet"]
    has_cache_dependencies(pdb, "probe_pet")
    assert probes["probe_pet"] is first


@pytest.mark.parametrize("which", ["cache", "dependency"])
def test_common_filters_on_the_cache_tables_disable_the_probe(pdb: t.Any, which: str) -> None:
    cache, dependency = cache_models(pdb)
    table = (cache if which == "cache" else dependency)._table
    calls: list[str] = []
    table._common_filter = lambda query: calls.append("filter") or (table.id > 0)
    try:
        assert has_cache_dependencies(pdb, "probe_pet")  # can't tell: the regular path hands the filter its own query
        pdb.statements.clear()
        ProbePet.insert(name="d")
    finally:
        table._common_filter = None

    assert calls  # the regular invalidation ran, with the filter
    assert not any("LIMIT 1" in s and "typedal_cache_dependency" in s for s in pdb.statements)


def test_hooks_on_the_cache_table_disable_the_probe(pdb: t.Any) -> None:
    cache, _ = cache_models(pdb)
    seen: list[str] = []
    cache._table._before_delete.append(lambda s: seen.append("cache delete"))
    try:
        assert has_cache_dependencies(pdb, "probe_pet")  # can't skip: the regular path runs the hook
        ProbePet.insert(name="d")
    finally:
        cache._table._before_delete.pop()

    assert seen == ["cache delete"]


WRITER = """
import sys
from src.typedal import TypeDAL
from tests.test_cache_probe import ProbePet
db = TypeDAL(sys.argv[1], folder=sys.argv[2], migrate=False, use_pyproject=False, use_env=False, attempts=1)
db.define(ProbePet)
print("pid", db.executesql("SELECT pg_backend_pid();")[0][0], flush=True)
for line in sys.stdin:
    cmd, *args = line.split()
    if cmd == "insert":
        ProbePet.insert(name=args[0])
    elif cmd == "update":
        db(db.probe_pet.name == args[0]).update(name=args[1])
    elif cmd == "delete":
        db(db.probe_pet.name == args[0]).delete()
    elif cmd == "commit":
        db.commit()
    print("ok", flush=True)
db.close()
"""


class Writer:
    """A second connection in a subprocess, driven line by line."""

    def __init__(self, uri: str, folder: Path) -> None:
        folder.mkdir()
        self.proc = subprocess.Popen(
            [sys.executable, "-c", WRITER, uri, str(folder)],
            cwd=Path(__file__).parent.parent,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        self.pid = int(self.read_line(60).split()[1])

    def read_line(self, timeout: float) -> str:
        assert self.proc.stdout
        ready, _, _ = select.select([self.proc.stdout], [], [], timeout)
        if not ready:
            self.proc.kill()
            pytest.fail("the writer stalled")
        return self.proc.stdout.readline().strip()

    def start(self, command: str) -> None:
        assert self.proc.stdin
        self.proc.stdin.write(command + "\n")
        self.proc.stdin.flush()

    def send(self, command: str) -> None:
        self.start(command)
        assert self.read_line(60) == "ok"

    def wait_for_lock_on(self, db: t.Any, relation: str) -> None:
        """Until the writer waits for a lock on `relation` (held by `db`)."""
        query = f"SELECT 1 FROM pg_locks WHERE pid = {self.pid} AND NOT granted AND relation = '{relation}'::regclass;"
        for _ in range(600):
            if db.executesql(query):
                return
            time.sleep(0.05)
        pytest.fail(f"the writer never waited for a lock on {relation}")  # pragma: no cover

    def close(self) -> None:
        assert self.proc.stdin
        self.proc.stdin.close()
        try:
            self.proc.wait(timeout=60)
        except subprocess.TimeoutExpired:  # pragma: no cover
            self.proc.kill()
            self.proc.wait()
        assert self.proc.returncode == 0


@pytest.fixture
def writer(compat_uri: str, tmp_path: Path, pdb: t.Any) -> t.Iterator[Writer]:
    if not is_postgres(pdb):
        pytest.skip("needs two PostgreSQL connections (READ COMMITTED, table locks)")
    w = Writer(compat_uri, tmp_path / "writer")
    try:
        yield w
    finally:
        pdb.rollback()  # release this connection's locks first, so a failed test doesn't leave the writer waiting
        w.close()


@pytest.mark.parametrize(("command", "after"), [("insert d", "abcd"), ("delete a", "bc")])
def test_read_committed_race_is_unchanged(pdb: t.Any, writer: Writer, command: str, after: str) -> None:
    writer.send(command)  # not committed; its invalidation already ran
    assert names() == ["a", "b", "c"]  # this reader can't see the change yet, and caches that
    pdb.commit()
    writer.send("commit")
    pdb.commit()
    # known limitation (also before the probe): a reader caching before the writer commits keeps a stale entry;
    # with the probe that window starts one statement earlier (accepted, DECISIONS row 1)
    assert names() == ["a", "b", "c"]
    assert sorted(r.name for r in pdb(pdb.probe_pet).select()) == list(after)


def test_delete_waiting_on_a_lock_invalidates_afterwards(pdb: t.Any, writer: Writer) -> None:
    pdb.executesql("LOCK TABLE probe_pet IN ACCESS EXCLUSIVE MODE;")
    writer.start("delete a")
    writer.wait_for_lock_on(pdb, "probe_pet")
    assert names() == ["a", "b", "c"]  # this reader caches while the writer waits
    pdb.commit()  # releases the lock: the writer looks up the ids, probes and deletes
    assert writer.read_line(60) == "ok"
    writer.send("commit")
    pdb.commit()
    assert names() == ["b", "c"]  # the delete's invalidation ran after the wait, so the entry is gone


@pytest.mark.parametrize(("command", "after"), [("insert d", "abcd"), ("update a z", "bcz"), ("delete a", "bc")])
def test_writes_wait_for_a_locked_cache_table(pdb: t.Any, writer: Writer, command: str, after: str) -> None:
    pdb.executesql("LOCK TABLE typedal_cache IN ACCESS EXCLUSIVE MODE;")  # as clear_cache's TRUNCATE does
    writer.start(command)
    writer.wait_for_lock_on(pdb, "typedal_cache")  # the probe waits like the regular invalidation did
    assert names() == ["a", "b", "c"]  # cached by the lock holder, before the writer commits
    pdb.commit()
    assert writer.read_line(60) == "ok"  # the probe now sees that entry and invalidates it
    writer.send("commit")
    pdb.commit()
    assert names() == sorted(after)


def test_a_failing_probe_raises(pdb: t.Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> t.Any:
        raise RuntimeError("fetch failed")

    monkeypatch.setattr(pdb._adapter, "fetchall", broken)
    with pytest.raises(RuntimeError, match="fetch failed"):
        ProbePet.insert(name="d")
