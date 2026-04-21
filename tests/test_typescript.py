import pytest
from configuraptor.singleton import SingletonMeta

from src.typedal import Ref, TypeDAL, TypedTable, relationship
from src.typedal.serializers.typescript import TypedDictRegistry

db = TypeDAL("sqlite:memory")


@db.define()
class SecondModel(TypedTable):
    key: str
    value: "int"

    first = relationship(Ref["FirstModel"])


@db.define()
class FirstModel(TypedTable):
    second: SecondModel


def test_typescript():
    # Create a world to collect types

    typescript_code = db.as_typescript()

    assert "interface FirstModel {" in typescript_code
    assert "interface SecondModel {" in typescript_code

    assert "first: FirstModel" in typescript_code
    assert "second: SecondModel" in typescript_code

    assert "unknown" not in typescript_code
    assert "any" not in typescript_code


def test_table_as_typescript():
    typescript_code = FirstModel.as_typescript()
    assert "interface FirstModel {" in typescript_code
    assert "interface SecondModel {" in typescript_code


def test_registry_world_and_duplicate_name_guard():
    SingletonMeta.clear()
    registry = TypedDictRegistry()
    assert registry.world is not None

    class DummyModel:
        pass

    registry.create(DummyModel, {"id": int}, name="DummyModel")
    registry.create(DummyModel, {"id": int}, name="DummyModel")

    assert "DummyModel" in registry._names

    SingletonMeta.clear()


def test_registry_get_typescript_without_world_warns():
    SingletonMeta.clear()
    registry = TypedDictRegistry()
    registry._world = None

    with pytest.warns(UserWarning, match="typedal\\[typescript\\]"):
        result = registry.get_typescript("as_typescript")

    assert result == ""
    SingletonMeta.clear()


def test_base_relationship_type_resolver_returns_none_without_type():
    class DummyRelationship:
        pass

    assert FirstModel._typedal_resolve_relationship_python_type(DummyRelationship()) is None
