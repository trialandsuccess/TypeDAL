"""
Pins how model instances are built and how their class and instance methods split, before performance step 5.

Step 5 makes hydration cheaper; these tests describe what an instance must look like and do afterwards.
"""

import json
import pickle
import typing as t
from pathlib import Path

import dill
import pytest
from pydal.objects import Row

from src.typedal import TypeDAL, TypedRows, TypedTable, relationship

from .compat_support import make_db


class InstOwner(TypedTable):
    name: str
    things = relationship(list["InstThing"], lambda self, other: other.owner == self.id)


class InstThing(TypedTable):
    name: str
    n: int | None
    owner: InstOwner | None


class InstWithSetup(TypedTable):
    name: str

    def _setup_instance_methods(self) -> None:
        super()._setup_instance_methods()
        setattr(self, "extra", "set up")


class InstWithoutSuper(TypedTable):
    name: str

    def _setup_instance_methods(self) -> None:
        setattr(self, "extra", "own only")


setattr_log: list[str] = []


class InstOwnSetattr(TypedTable):
    name: str

    def __setattr__(self, key: str, value: t.Any) -> None:
        setattr_log.append(key)
        super().__setattr__(key, value)


class InstPatchedLater(TypedTable):
    name: str


override_log: list[str] = []


class InstPublicOverride(TypedTable):
    name: str

    def update_record(self, **fields: t.Any) -> t.Any:
        override_log.append("update_record")
        return self._update_record(**fields)


class MyRow(Row):
    pass


@pytest.fixture
def idb(tmp_path: Path) -> t.Iterator[TypeDAL]:
    db = make_db(f"sqlite://{tmp_path / 'inst.db'}", tmp_path, enable_typedal_caching=False)
    try:
        for model in (
            InstOwner,
            InstThing,
            InstWithSetup,
            InstWithoutSuper,
            InstOwnSetattr,
            InstPatchedLater,
            InstPublicOverride,
        ):
            db.define(model)
        InstOwner.insert(name="o")
        InstThing.insert(name="a", n=1, owner=1)
        InstThing.insert(name="b", n=None, owner=None)
        for model in (InstWithSetup, InstWithoutSuper, InstOwnSetattr, InstPatchedLater, InstPublicOverride):
            model.insert(name="x")
        setattr_log.clear()
        yield db
    finally:
        db.close()


METHODS = ["as_dict", "__json__", "as_json", "as_xml", "update"]


def first(model: type[TypedTable]) -> t.Any:
    return model.orderby(model.id).first()


def test_instance_dict_contents_and_order(idb: TypeDAL) -> None:
    from_first = first(InstThing)
    from_id = InstThing(1)
    from_collect = InstThing.orderby(InstThing.id).collect().first()

    # the row's keys in order (PyDAL puts update_record/delete_record after id), then the bound methods:
    row_keys = ["id", "update_record", "delete_record", "name", "n", "owner"]
    assert list(from_first.__dict__) == ["_row", *row_keys, *METHODS, "_rows"]
    assert list(from_id.__dict__) == ["_row", *row_keys, *METHODS]
    assert list(from_collect.__dict__) == ["_row", *row_keys, *METHODS, "_rows"]

    for inst in (from_first, from_id, from_collect):
        data = inst.__dict__
        assert data["_row"] is inst._row
        assert (data["id"], data["name"], data["n"], data["owner"]) == (1, "a", 1, 1)
        for name in [*METHODS, "update_record", "delete_record"]:
            assert getattr(data[name], "__self__", None) is inst, name


def test_dict_keys_iter_and_repr(idb: TypeDAL) -> None:
    inst = first(InstThing)

    assert inst.keys() == ["id", "update_record", "delete_record", "name", "n", "owner"]
    assert list(inst) == inst.keys()
    as_dict = dict(inst)
    assert list(as_dict) == inst.keys()
    assert (as_dict["id"], as_dict["name"], as_dict["n"], as_dict["owner"]) == (1, "a", 1, 1)
    assert as_dict["update_record"] == inst.update_record
    assert repr(inst) == '<InstThing({"id": 1, "name": "a", "n": 1, "owner": 1})>'
    assert inst.as_dict() == {"id": 1, "name": "a", "n": 1, "owner": 1}
    assert json.loads(inst.as_json()) == {"id": 1, "name": "a", "n": 1, "owner": 1}
    assert inst["name"] == inst.get("name") == inst.name == "a"
    assert inst.get("missing", 7) == 7
    with pytest.raises(AttributeError):
        inst.missing
    assert (bool(inst), int(inst)) == (True, 1)
    assert inst == InstThing(1)
    assert inst != first(InstOwner)


def test_methods_on_the_class_and_on_an_instance(idb: TypeDAL) -> None:
    inst = first(InstThing)

    # the class keeps its own variants however many instances were built:
    InstThing.collect()
    assert "tablename" in InstThing.as_dict()
    assert InstThing.as_json.__self__ is InstThing
    assert InstThing.update(InstThing.id == 2, name="b2") is not None
    assert InstThing(2).name == "b2"

    # instance variants work on the row:
    assert inst.update(name="local") is inst
    assert inst.name == "local" and inst._row.name == "local"
    assert InstThing(1).name == "a"  # update() doesn't write

    assert inst.update_record(name="saved") is inst
    assert inst.name == "saved" and InstThing(1).name == "saved"

    fresh = InstThing(1)
    assert fresh.delete_record() == 1
    assert not fresh
    assert InstThing(1) is None
    with pytest.raises(EnvironmentError):
        fresh.as_dict()


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="pre-existing: update_record copies PyDAL's RecordDeleter into the instance",
)
def test_delete_record_after_update_record(idb: TypeDAL) -> None:
    inst = first(InstThing)
    inst.update_record(name="saved")
    # preconditions raise RuntimeError, so only the known stale-instance symptom counts as the expected failure:
    if InstThing(1).name != "saved":
        raise RuntimeError("update_record did not write")
    if inst.delete_record() != 1 or InstThing(1) is not None:
        raise RuntimeError("delete_record did not delete")
    assert not inst


def test_assigning_attributes(idb: TypeDAL) -> None:
    inst = first(InstThing)

    inst.name = "changed"
    assert inst._row.name == "changed"
    inst.update_record()
    assert InstThing(1).name == "changed"

    inst.n = lambda: 1  # callables stay on the instance only
    assert callable(inst.n) and inst._row.n == 1
    inst.not_a_column = 5
    assert "not_a_column" not in inst._row
    inst["name"] = "via item"
    assert inst._row.name == "via item"


def test_constructors(idb: TypeDAL) -> None:
    inst = InstThing(1)
    assert InstThing(id=1) == InstThing(InstThing.id == 1) == InstThing(name="a") == inst
    assert InstThing(inst) is inst
    assert InstThing(999) is None
    dummy = InstThing()
    assert not dummy
    assert dummy._row is None
    assert "as_dict" not in dummy.__dict__


def test_overridden_setup_instance_methods(idb: TypeDAL) -> None:
    for inst in (first(InstWithSetup), InstWithSetup(1), InstWithSetup.collect().first()):
        assert inst.extra == "set up"
        assert inst.as_dict() == {"id": 1, "name": "x"}

    without = first(InstWithoutSuper)
    assert without.extra == "own only"
    assert "as_dict" not in without.__dict__
    # without the instance variant, as_dict resolves to the classmethod (the table definition):
    assert "tablename" in without.as_dict()


def test_own_setattr_sees_every_assignment(idb: TypeDAL) -> None:
    inst = first(InstOwnSetattr)
    built = list(setattr_log)
    inst.name = "y"

    assert built == ["_row", *METHODS, "delete_record", "update_record", "_rows"]
    assert setattr_log[len(built) :] == ["name"]
    assert inst._row.name == "y"


def test_setattr_patched_after_define(idb: TypeDAL) -> None:
    log: list[str] = []
    original = InstPatchedLater.__dict__.get("__setattr__")

    def patched(self: t.Any, key: str, value: t.Any) -> None:
        log.append(key)
        TypedTable.__setattr__(self, key, value)

    InstPatchedLater.__setattr__ = patched  # type: ignore[method-assign]
    try:
        inst = InstPatchedLater(1)
        inst.name = "z"
    finally:
        if original is None:
            del InstPatchedLater.__setattr__
        else:  # pragma: no cover
            InstPatchedLater.__setattr__ = original  # type: ignore[method-assign]

    assert log == ["_row", *METHODS, "delete_record", "update_record", "name"]
    assert inst._row.name == "z"


def test_rows_with_extra_columns(idb: TypeDAL) -> None:
    inst = InstThing.select(InstThing.id, InstThing.name, InstThing.id.count()).groupby(InstThing.id).collect().first()

    assert inst is not None
    assert list(inst.__dict__) == ["_row", "id", "update_record", "delete_record", "name", *METHODS, "_rows"]
    assert "_extra" in inst._row
    assert inst.as_dict() == inst._row.as_dict()
    assert inst.name == "a"


def test_custom_row_class(idb: TypeDAL) -> None:
    inst = InstThing(MyRow(id=5, name="z", n=None))

    assert type(inst._row) is MyRow
    assert list(inst.__dict__) == ["_row", "id", "name", "n", *METHODS, "delete_record", "update_record"]
    inst.name = "q"
    assert inst._row.name == "q"

    plain = InstThing(Row(id=6, name="p"))
    assert list(plain.__dict__) == ["_row", "id", "name", *METHODS, "delete_record", "update_record"]


def test_pickling(idb: TypeDAL) -> None:
    inst = first(InstThing)

    for copy in (pickle.loads(pickle.dumps(inst)), dill.loads(dill.dumps(inst))):
        assert list(copy.__dict__) == ["id", "name", "n", "owner", "_row", *METHODS, "delete_record", "update_record"]
        assert copy.as_dict() == {"id": 1, "name": "a", "n": 1, "owner": 1}
        assert copy.as_dict.__self__ is copy
        assert copy == inst


def test_relationship_instances(idb: TypeDAL) -> None:
    owner = InstOwner.join("things").first()

    assert owner is not None
    assert owner._with == ["things"]
    assert owner.keys()[-1] == "things"
    assert [t_.name for t_ in owner.things] == ["a"]
    assert isinstance(owner.things[0], InstThing)
    assert owner.things[0].as_dict.__self__ is owner.things[0]

    copy = dill.loads(dill.dumps(owner))
    assert copy._with == ["things"]
    assert [t_.name for t_ in copy.things] == ["a"]


def test_from_rows_with_into_and_init(idb: TypeDAL) -> None:
    rows = idb(idb.inst_thing).select(orderby=idb.inst_thing.id)
    seen: list[t.Any] = []

    typed = TypedRows.from_rows(rows, InstThing, into=InstWithSetup, init=lambda inst, row: seen.append(row.id))

    assert seen == [1, 2]
    assert [type(r).__name__ for r in typed] == ["InstWithSetup", "InstWithSetup"]
    assert [r.extra for r in typed] == ["set up", "set up"]
    assert list(typed.records) == [1, 2]


@pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="pre-existing: the bound _update_record in __dict__ hides the override"
)
def test_public_method_override_on_instances(idb: TypeDAL) -> None:
    override_log.clear()
    inst = first(InstPublicOverride)
    inst.update_record(name="y")

    assert InstPublicOverride(1).name == "y"
    assert override_log == ["update_record"]
