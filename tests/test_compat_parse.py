"""
Row parsing on a TypeDAL database must match plain PyDAL for every field type, NULLs and select shape.

The reference is a plain `pydal.DAL` on the same database with the same table definitions (cloned field by field).
"""

import datetime as dt
import decimal
import typing as t
from pathlib import Path

import pytest
from pydal import DAL, Field, SQLCustomType

from src.typedal import TypeDAL, TypedField, TypedTable, relationship

from .compat_support import compat_uri, drop_all, make_db, norm

__all__ = ["compat_uri"]


class CompatOwner(TypedTable):
    name: str
    items = relationship(list["CompatItem"], lambda self, other: other.owner == self.id)


class CompatItem(TypedTable):
    s: str | None
    i: int | None
    f: float | None
    b: bool | None
    dec: decimal.Decimal | None
    d: dt.date | None
    tm: dt.time | None
    dtm: dt.datetime | None
    ls: list[str] | None
    li: list[int] | None
    j: object | None
    bl: bytes | None
    owner: CompatOwner | None
    fo_f = TypedField(float, notnull=False, filter_out=lambda v: ("FO", v))
    fo_b = TypedField(bool, notnull=False, filter_out=lambda v: ("FO", v))


def define_pydal_tables(db: DAL, log: list[t.Any]) -> None:
    """Every PyDAL field type, with filter_out on several types and a custom type that logs its decoder."""
    custom = SQLCustomType(
        type="string",
        native="text",
        encoder=lambda x: "'%s'" % ("E" + str(x)),
        decoder=lambda x: log.append(("decode", x)) or ("D", x),
    )

    def tagged(name: str) -> t.Callable[[t.Any], t.Any]:
        return lambda v: log.append((name, v)) or (name, v)

    db.define_table("p_owner", Field("name"))
    db.define_table(
        "p_thing",
        Field("s"),
        Field("txt", "text"),
        Field("i", "integer"),
        Field("bi", "bigint"),
        Field("f", "double"),
        Field("dec", "decimal(10,2)"),
        Field("b", "boolean"),
        Field("d", "date"),
        Field("tm", "time"),
        Field("dtm", "datetime"),
        Field("j", "json"),
        Field("ls", "list:string"),
        Field("li", "list:integer"),
        Field("lr", "list:reference p_owner"),
        Field("o", "reference p_owner"),
        Field("bl", "blob"),
        Field("up", "upload"),
        Field("pw", "password"),
        Field("c", custom),
        Field("fo_s", "string", filter_out=tagged("fo_s")),
        Field("fo_i", "integer", filter_out=tagged("fo_i")),
        Field("fo_f", "double", filter_out=tagged("fo_f")),
        Field("fo_b", "boolean", filter_out=tagged("fo_b")),
        Field("fo_dtm", "datetime", filter_out=tagged("fo_dtm")),
        Field("fo_j", "json", filter_out=tagged("fo_j")),
        Field("fl", "float"),
        *(
            [Field("jb", "jsonb"), Field("fo_jb", "jsonb", filter_out=tagged("fo_jb"))]
            if db._dbname == "postgres"
            else []
        ),
        Field.Virtual("vv", lambda r: r.p_thing.i),
        Field.Method("mm", lambda r: 1),
    )

    class OldStyle:
        def double_i(self) -> t.Any:
            return None if self.p_thing.i is None else self.p_thing.i * 2

    db.p_thing.virtualfields.append(OldStyle())


@pytest.fixture
def dbs(compat_uri: str, tmp_path: Path) -> t.Iterator[tuple[TypeDAL, DAL, list[t.Any], list[t.Any]]]:
    db_log: list[t.Any] = []
    ref_log: list[t.Any] = []
    db = make_db(compat_uri, tmp_path, enable_typedal_caching=False)
    ref = None
    try:
        ref = DAL(compat_uri, folder=str(tmp_path), migrate=False, attempts=1)
        define_pydal_tables(db, db_log)
        db.define(CompatOwner)
        db.define(CompatItem)
        define_pydal_tables(ref, ref_log)
        for name in ("compat_owner", "compat_item"):
            table = db[name]
            ref.define_table(name, *[f.clone() for f in table if f.type != "id"], migrate=False)
        fill(db)
        db.commit()
        db_log.clear()
        yield db, ref, db_log, ref_log
    finally:
        try:
            if ref is not None:
                ref.close()
        finally:
            drop_all(db)


def fill(db: TypeDAL) -> None:
    o1 = db.p_owner.insert(name="o1")
    o2 = db.p_owner.insert(name="o2")
    full = dict(
        s="s",
        txt="t" * 300,
        i=7,
        bi=2**40,
        f=1.5,
        dec=decimal.Decimal("1.25"),
        b=True,
        d=dt.date(2020, 1, 2),
        tm=dt.time(3, 4, 5),
        dtm=dt.datetime(2020, 1, 2, 3, 4, 5, 123456),
        j={"a": [1, None, "x"]},
        ls=["x", "y"],
        li=[1, 2],
        lr=[o1, o2],
        o=o1,
        bl=b"\x00\xff",
        up="file.txt",
        pw="secret",
        c="cv",
        fo_s="raw",
        fo_i=3,
        fo_f=2.5,
        fo_b=False,
        fo_dtm=dt.datetime(2021, 5, 6, 7, 8, 9),
        fo_j={"k": 1},
        fl=0.5,
    )
    edge = dict(
        s="",
        txt="ü€😀",
        i=0,
        bi=-1,
        f=0.0,
        dec=decimal.Decimal("0"),
        b=False,
        j=[],
        ls=[],
        li=[],
        lr=[],
        o=o2,
        bl=b"",
        c="",
        fo_s="",
        fo_i=0,
        fo_f=0.0,
        fo_b=True,
        fo_j=None,
        fl=-1.25,
    )
    if db._dbname == "postgres":
        full |= dict(jb={"n": {"x": [1, {"y": None}]}, "s": "t"}, fo_jb={"k": [2]})
        edge |= dict(jb=[], fo_jb=None)
    db.p_thing.insert(**full)
    db.p_thing.insert()  # every column NULL (or its default)
    db.p_thing.insert(**edge)
    db.p_thing.insert(s=None, o=None, fo_f=None, fo_b=None)

    db.compat_owner.insert(name="a")
    db.compat_owner.insert(name="b")
    db.compat_item.insert(
        s="x",
        i=1,
        f=1.5,
        b=True,
        dec=decimal.Decimal("2.50"),
        d=dt.date(2020, 1, 2),
        tm=dt.time(1, 2, 3),
        dtm=dt.datetime(2020, 1, 2, 3, 4, 5),
        ls=["a"],
        li=[1],
        j={"x": 1},
        owner=1,
        fo_f=1.0,
        fo_b=False,
    )
    db.compat_item.insert(owner=1)
    db.compat_item.insert(s="", i=0, b=False, owner=None)


SELECTS: dict[str, t.Callable[[t.Any], t.Any]] = {
    "plain": lambda db: db(db.p_thing).select(orderby=db.p_thing.id),
    "cacheable": lambda db: db(db.p_thing).select(orderby=db.p_thing.id, cacheable=True),
    "some_fields": lambda db: db(db.p_thing).select(
        db.p_thing.id, db.p_thing.j, db.p_thing.fo_f, orderby=db.p_thing.id
    ),
    "no_id": lambda db: db(db.p_thing).select(db.p_thing.s, db.p_thing.fo_b, db.p_thing.c, orderby=db.p_thing.id),
    "inner_join": lambda db: db(db.p_thing.o == db.p_owner.id).select(
        db.p_thing.id, db.p_thing.dtm, db.p_owner.name, db.p_thing.lr, orderby=db.p_thing.id
    ),
    "inner_join_all": lambda db: db(db.p_thing.o == db.p_owner.id).select(orderby=db.p_thing.id | db.p_owner.id),
    "left_join": lambda db: db(db.p_thing).select(
        db.p_thing.s,
        db.p_thing.fo_i,
        db.p_owner.name,
        left=db.p_owner.on(db.p_thing.o == db.p_owner.id),
        orderby=db.p_thing.id,
    ),
    "aggregate": lambda db: db(db.p_thing).select(
        db.p_thing.b,
        db.p_thing.i.sum().with_alias("total"),
        db.p_thing.id.count(),
        db.p_thing.dtm.max(),
        groupby=db.p_thing.b,
        orderby=db.p_thing.b,
    ),
    "field_alias": lambda db: db(db.p_thing).select(
        db.p_thing.s.with_alias("ss"), db.p_thing.fo_f.with_alias("ff"), db.p_thing.b, orderby=db.p_thing.id
    ),
    "table_alias": lambda db: db(db.p_thing.with_alias("t2")).select(
        db.p_thing.with_alias("t2").id,
        db.p_thing.with_alias("t2").dtm,
        db.p_thing.with_alias("t2").fo_j,
        orderby=db.p_thing.with_alias("t2").id,
    ),
    "distinct": lambda db: db(db.p_thing).select(db.p_thing.o, distinct=True, orderby=db.p_thing.o),
    "nested": lambda db: db(db.p_thing.o.belongs(db(db.p_owner.name == "o1")._select(db.p_owner.id))).select(
        orderby=db.p_thing.id
    ),
    "limit": lambda db: db(db.p_thing).select(limitby=(1, 3), orderby=db.p_thing.id),
    "executesql_fields": lambda db: db.executesql(
        "SELECT id, j, dtm, fo_f, c FROM p_thing ORDER BY id;",
        fields=[db.p_thing.id, db.p_thing.j, db.p_thing.dtm, db.p_thing.fo_f, db.p_thing.c],
    ),
    "executesql_colnames": lambda db: db.executesql(
        "SELECT id, s FROM p_thing ORDER BY id;", colnames=["p_thing.id", "p_thing.s"]
    ),
    "executesql_dict": lambda db: db.executesql("SELECT id, s, i FROM p_thing ORDER BY id;", as_dict=True),
    "executesql_raw": lambda db: db.executesql("SELECT id, s, i FROM p_thing ORDER BY id;"),
    "as_list": lambda db: db(db.p_thing).select(orderby=db.p_thing.id).as_list(),
    "as_dict": lambda db: db(db.p_thing).select(orderby=db.p_thing.id).as_dict(),
    "first_as_dict": lambda db: db(db.p_thing).select(orderby=db.p_thing.id).first().as_dict(),
    "virtual_and_methods": lambda db: [
        (r.vv, r.mm(), r.double_i) for r in db(db.p_thing).select(orderby=db.p_thing.id)
    ],
    "follow_reference": lambda db: [r.o.name if r.o else None for r in db(db.p_thing).select(orderby=db.p_thing.id)],
    "referenced_by": lambda db: db.p_owner(1).p_thing.select(orderby=db.p_thing.id),
    "by_id": lambda db: db.p_thing(1),
    "by_index": lambda db: db.p_thing[3],
    "model_table": lambda db: db(db.compat_item).select(orderby=db.compat_item.id),
    "model_join": lambda db: db(db.compat_item.owner == db.compat_owner.id).select(orderby=db.compat_item.id),
}


@pytest.mark.parametrize("name", sorted(SELECTS))
def test_select_matches_pydal(dbs: tuple[TypeDAL, DAL, list[t.Any], list[t.Any]], name: str) -> None:
    db, ref, db_log, ref_log = dbs
    expected = norm(SELECTS[name](ref))
    actual = norm(SELECTS[name](db))

    assert actual == expected
    # filter_out and custom decoders see the same values in the same order:
    assert db_log == ref_log


@pytest.mark.parametrize("table", ["p_thing", "p_owner"])
@pytest.mark.xfail(strict=True, raises=TypeError, reason="pre-existing: Rows.render() deep-copies a lock on TypeDAL")
def test_render_matches_pydal(dbs: tuple[TypeDAL, DAL, list[t.Any], list[t.Any]], table: str) -> None:
    db, ref, _, _ = dbs
    expected = norm(list(ref(ref[table]).select(orderby=ref[table].id).render()))
    actual = norm(list(db(db[table]).select(orderby=db[table].id).render()))
    assert actual == expected


def test_field_object_and_nested_access(dbs: tuple[TypeDAL, DAL, list[t.Any], list[t.Any]]) -> None:
    db, ref, db_log, ref_log = dbs

    def access(d: t.Any) -> t.Any:
        joined = d(d.p_thing.o == d.p_owner.id).select(d.p_thing.ALL, d.p_owner.name, orderby=d.p_thing.id)
        plain = d(d.p_thing).select(orderby=d.p_thing.id)
        return [
            [
                (r[d.p_thing.s], r[d.p_owner.name], r.p_thing.id, r["p_thing"]["fo_f"], r.p_owner["name"])
                for r in joined
            ],
            [(r[d.p_thing.s], r[d.p_thing.fo_b], r["j"], r.get("c"), r[d.p_thing.dtm]) for r in plain],
            [
                (r.p_thing.s, r.p_thing.vv)
                for r in d(d.p_thing).select(
                    d.p_thing.ALL, d.p_thing.id.count(), groupby=d.p_thing.id, orderby=d.p_thing.id
                )
            ],
        ]

    assert norm(access(db)) == norm(access(ref))
    assert db_log == ref_log


def test_record_operators_on_parsed_rows(dbs: tuple[TypeDAL, DAL, list[t.Any], list[t.Any]]) -> None:
    db, _, _, _ = dbs
    first, *_, last = db(db.p_thing).select(orderby=db.p_thing.id)

    first.update_record(s="changed")
    assert db.p_thing(first.id).s == "changed"
    assert last.delete_record() == 1
    assert db.p_thing(last.id) is None

    owner = db(db.p_owner).select(orderby=db.p_owner.id).first()
    assert [r.id for r in owner.p_thing.select(orderby=db.p_thing.id)] == [1]
    assert owner.p_thing.count() == 1


def test_models_hydrate_from_the_same_rows(dbs: tuple[TypeDAL, DAL, list[t.Any], list[t.Any]]) -> None:
    db, ref, _, _ = dbs
    expected = ref(ref.compat_item).select(orderby=ref.compat_item.id)
    items = CompatItem.orderby(CompatItem.id).collect()

    assert len(items) == len(expected)
    for item, row in zip(items, expected):
        assert norm(item._row) == norm(row)
        for name in db.compat_item.fields:
            assert norm(getattr(item, name)) == norm(row[name])
            assert norm(item[name]) == norm(row[name])

    # filter_out also runs for NULL values on model fields:
    assert [(i.fo_f, i.fo_b) for i in items] == [
        (("FO", 1.0), ("FO", False)),
        (("FO", None), ("FO", None)),
        (("FO", None), ("FO", None)),
    ]


def test_model_relationship_join(dbs: tuple[TypeDAL, DAL, list[t.Any], list[t.Any]]) -> None:
    owners = CompatOwner.join("items").orderby(CompatOwner.id).collect()

    assert [o.name for o in owners] == ["a", "b"]
    assert [sorted((i.id, i.s, i.fo_f) for i in o.items) for o in owners] == [
        [(1, "x", ("FO", 1.0)), (2, None, ("FO", None))],
        [],
    ]
    first = owners.first()
    assert first is not None
    item = min(first.items, key=lambda i: i.id)
    assert isinstance(item, CompatItem)
    assert item.dtm == dt.datetime(2020, 1, 2, 3, 4, 5)


def test_model_select_subset_and_column(dbs: tuple[TypeDAL, DAL, list[t.Any], list[t.Any]]) -> None:
    _, ref, _, _ = dbs
    subset = CompatItem.select(CompatItem.id, CompatItem.j, CompatItem.fo_b).orderby(CompatItem.id).collect()
    expected = ref(ref.compat_item).select(
        ref.compat_item.id, ref.compat_item.j, ref.compat_item.fo_b, orderby=ref.compat_item.id
    )
    assert [norm(i._row) for i in subset] == [norm(r) for r in expected]
    assert CompatItem.orderby(CompatItem.id).column(CompatItem.dtm) == [
        dt.datetime(2020, 1, 2, 3, 4, 5),
        None,
        None,
    ]
    assert norm(CompatItem.orderby(CompatItem.id).column(CompatItem.fo_f)) == norm(
        [r.fo_f for r in ref(ref.compat_item).select(ref.compat_item.fo_f, orderby=ref.compat_item.id)]
    )
