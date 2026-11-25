"""Deletes fetch the ids they need as raw values, without building a Row per id."""

import typing as t
from pathlib import Path

import pytest

from pydal.adapters import SQLite, adapters
from pydal.objects import Row, Rows

from src.typedal import TypeDAL, TypedTable
from src.typedal.set_types import UpdateSet
from src.typedal.helpers import first_column, plain_rows

from .compat_support import compat_uri, drop_all, make_db

__all__ = ["compat_uri"]


class DeleteItem(TypedTable):
    name: str


@pytest.fixture
def ddb(compat_uri: str, tmp_path: Path) -> t.Iterator[TypeDAL]:
    db = make_db(compat_uri, tmp_path, enable_typedal_caching=True)
    try:
        db.define(DeleteItem)
        for name in "abcde":
            DeleteItem.insert(name=name)
        db.commit()
        yield db
    finally:
        drop_all(db)


def test_first_column() -> None:
    assert first_column([(1, "x"), (2, "y")], [], ["a", "b"], blob_decode=True, cacheable=False) == [1, 2]
    assert first_column([]) == []


@pytest.mark.parametrize("cached", [False, True])
def test_deletes_do_not_parse_rows(ddb: TypeDAL, monkeypatch: pytest.MonkeyPatch, cached: bool) -> None:
    if cached:
        DeleteItem.where(DeleteItem.id > 0).cache().collect()  # so the cache hook looks up the ids as well
    built: list[set[str]] = []
    init = Row.__init__
    monkeypatch.setattr(Row, "__init__", lambda self, *a, **kw: built.append(set(kw)) or init(self, *a, **kw))

    ids = DeleteItem.where(DeleteItem.name.belongs(["b", "d"])).delete()
    ddb(ddb.delete_item.name == "e").delete()

    assert sorted(ids) == [2, 4]
    assert all(type(i) is int for i in ids)
    # TypeDAL's own `select("id")` (delete, cache hook, cache model delete) builds no Row(_extra=...)
    assert {"_extra"} not in built
    assert sorted(r.name for r in ddb(ddb.delete_item).select()) == ["a", "c"]


class NormalizedRow(Row):
    def __getitem__(self, key: str) -> t.Any:
        value = super().__getitem__(key)
        return value * 10 if key == "id" and "_extra" in self.__dict__ else value


class ReversedRows(Rows):
    def __iter__(self) -> t.Iterator[t.Any]:
        yield from reversed(list(super().__iter__()))


class NormalizedRowDAL(TypeDAL):
    Row = NormalizedRow


class ReversedRowsDAL(TypeDAL):
    Rows = ReversedRows


class NormalizingSQLite(SQLite):
    def parse(self, rows: t.Any, fields: t.Any, colnames: list[str], *args: t.Any, **kwargs: t.Any) -> t.Any:
        if colnames == ["id"]:
            rows = [(row[0] * 10,) for row in rows]
        return super().parse(rows, fields, colnames, *args, **kwargs)


class ResolvingSQLite(SQLite):
    def expand_all(self, fields: t.Any, tabledict: t.Any) -> t.Any:
        if tuple(fields) == ("id",) and len(tabledict) == 1:
            return [next(iter(tabledict.values()))._id]
        return super().expand_all(fields, tabledict)


ADAPTERS = {"adapter": NormalizingSQLite, "expansion": ResolvingSQLite}


@pytest.mark.parametrize("custom", ["row", "adapter", "expansion"])
def test_custom_parsing_still_builds_the_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, custom: str) -> None:
    if custom in ADAPTERS:
        monkeypatch.setitem(adapters._registry_, "sqlite", ADAPTERS[custom])
    cls = NormalizedRowDAL if custom == "row" else TypeDAL
    db = make_db(f"sqlite://{tmp_path / 'custom.db'}", tmp_path, cls=cls, enable_typedal_caching=True)
    try:
        db.define(DeleteItem)
        for name in "abc":
            DeleteItem.insert(name=name)
        db.delete_item.id.filter_out = lambda value: value * 10  # cache dependencies are stored under these ids
        query = DeleteItem.where(DeleteItem.name != "").cache()
        assert sorted(r.name for r in query.collect()) == ["a", "b", "c"]
        assert query.collect().metadata["cache"]["status"] == "cached"

        assert db(db.delete_item.name == "a").delete() == 1
        assert sorted(r.name for r in query.collect()) == ["b", "c"]  # the cache hook found the dependency
        assert DeleteItem.where(name="b").delete() == [20]  # and the custom parsing built the returned id
    finally:
        db.close()


def test_custom_rows_keep_their_order(tmp_path: Path) -> None:
    db = make_db(f"sqlite://{tmp_path / 'custom.db'}", tmp_path, cls=ReversedRowsDAL, enable_typedal_caching=False)
    try:
        db.define(DeleteItem)
        for name in "abc":
            DeleteItem.insert(name=name)
        assert DeleteItem.where(DeleteItem.name != "").delete() == [3, 2, 1]
    finally:
        db.close()


def test_plain_rows_only_for_pydals_own_adapters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = make_db(f"sqlite://{tmp_path / 'plain.db'}", tmp_path, enable_typedal_caching=False)
    try:
        db.define(DeleteItem)
        assert plain_rows(db(db.delete_item))
        monkeypatch.setattr(db._adapter, "rowslice", lambda rows, *a: rows)  # an instance-level override
        assert not plain_rows(db(db.delete_item))
    finally:
        db.close()
    monkeypatch.setitem(adapters._registry_, "sqlite", type("PlainSubclass", (SQLite,), {}))
    db = make_db(f"sqlite://{tmp_path / 'sub.db'}", tmp_path, enable_typedal_caching=False)
    try:
        db.define(DeleteItem)
        assert not plain_rows(db(db.delete_item))  # a subclass outside pydal may change anything on the way
    finally:
        db.close()


class ReversedSet(UpdateSet):
    def select(self, *fields: t.Any, **attributes: t.Any) -> t.Any:
        rows = super().select(*fields, **attributes)
        if fields == ("id",):
            rows.records.reverse()
        return rows


class ReversedSetDAL(TypeDAL):
    def where(self, query: t.Any = None, ignore_common_filters: bool | None = None) -> t.Any:
        return ReversedSet(self, super().where(query, ignore_common_filters).query)


@pytest.mark.parametrize("cached", [False, True])
def test_custom_set_select_still_builds_the_ids(tmp_path: Path, cached: bool) -> None:
    db = make_db(f"sqlite://{tmp_path / 'set.db'}", tmp_path, cls=ReversedSetDAL, enable_typedal_caching=cached)
    try:
        db.define(DeleteItem)
        DeleteItem.insert(name="one")
        DeleteItem.insert(name="two")
        if cached:
            query = DeleteItem.where(DeleteItem.id > 0).cache()
            assert len(query.collect()) == 2
            assert db(db.delete_item.id > 0).delete() == 2
            assert len(query.collect()) == 0
        else:
            assert DeleteItem.where(DeleteItem.id > 0).delete() == [2, 1]
    finally:
        db.close()


def test_instance_column_parser_still_builds_the_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = make_db(f"sqlite://{tmp_path / 'cols.db'}", tmp_path, enable_typedal_caching=False)
    try:
        db.define(DeleteItem)
        DeleteItem.insert(name="one")
        db.delete_item.id.filter_out = lambda value: value * 10
        expand = db._adapter._parse_expand_colnames

        def resolve_bare_id(fields: list[t.Any]) -> t.Any:
            if len(fields) == 1 and not isinstance(fields[0], type(db.delete_item.id)):
                return expand([db.delete_item.id])
            return expand(fields)

        monkeypatch.setattr(db._adapter, "_parse_expand_colnames", resolve_bare_id)
        assert DeleteItem.where(DeleteItem.name == "one").delete() == [10]
    finally:
        db.close()
