import enum
import io
import textwrap
from typing import Literal

import pytest
from pydal2sql_core import RenderContext, render_schema_from_code

from src.typedal import Ref, TypeDAL, TypedField, TypedTable, relationship
from src.typedal.serializers.typescript import TypedDictRegistry

db = TypeDAL("sqlite:memory")


class SomeEnum(enum.StrEnum):
    FIRST = "one"
    SECOND = "two"


@db.define()
class SecondModel(TypedTable):
    key: str
    value: "int"

    first = relationship(Ref["FirstModel"])


@db.define()
class FirstModel(TypedTable):
    second: SecondModel
    secret = TypedField(str, readable=False)


@db.define()
class Standalone(TypedTable):
    single: str
    first_choice: Literal[1, 2]
    second_choice: SomeEnum


def test_typescript():
    # Create a world to collect types

    typescript_code = db.as_typescript()

    assert "interface FirstModel {" in typescript_code
    assert "interface SecondModel {" in typescript_code

    assert "first: FirstModel" in typescript_code
    assert "second: SecondModel" in typescript_code

    assert "unknown" not in typescript_code
    assert "any" not in typescript_code
    assert "secret" not in typescript_code

    assert "1 | 2" in typescript_code
    assert "enum SomeEnum" in typescript_code


def test_typescript_filtered():
    typescript_code1 = db.as_typescript("standalone")
    typescript_code2 = db.as_typescript("Standalone")
    typescript_code3 = db.as_typescript(Standalone)

    # assert typescript_code1 == typescript_code2 == typescript_code3
    assert typescript_code1 == typescript_code2, "first two"
    assert typescript_code2 == typescript_code3, "second two"
    assert "interface Standalone {" in typescript_code1
    assert "interface FirstModel {" not in typescript_code2
    assert "interface SecondModel {" not in typescript_code3


def test_table_as_typescript():
    typescript_code = FirstModel.as_typescript()
    assert "interface FirstModel {" in typescript_code
    assert "interface SecondModel {" in typescript_code


def test_table_as_typescript_isolated_per_call():
    TypedDictRegistry.clear()

    _ = FirstModel.as_typescript()
    standalone_typescript = Standalone.as_typescript()

    assert "interface Standalone {" in standalone_typescript
    assert "interface FirstModel {" not in standalone_typescript
    assert "interface SecondModel {" not in standalone_typescript

    TypedDictRegistry.clear()


def test_table_as_typescript_resolves_wrapped_related_models():
    TypedDictRegistry.clear()
    isolated_db = TypeDAL("sqlite:memory")

    @isolated_db.define()
    class Child(TypedTable):
        value: int

    @isolated_db.define()
    class Parent(TypedTable):
        child: Child | None
        children: list[Child]

    typescript_code = Parent.as_typescript()

    assert "interface Parent {" in typescript_code
    assert "interface Child {" in typescript_code

    TypedDictRegistry.clear()


def test_registry_world_and_duplicate_name_guard():
    TypedDictRegistry.clear()
    registry = TypedDictRegistry()
    assert registry.world is not None

    class DummyModel:
        pass

    registry.create(DummyModel, {"id": int}, name="DummyModel")
    registry.create(DummyModel, {"id": int}, name="DummyModel")

    assert "DummyModel" in registry._names

    TypedDictRegistry.clear()


def test_registry_get_typescript_without_world_warns():
    TypedDictRegistry.clear()
    registry = TypedDictRegistry()
    registry._world = None

    with pytest.warns(UserWarning, match="typedal\\[typescript\\]"):
        result = registry.get_typescript("as_typescript")

    assert result == ""
    TypedDictRegistry.clear()


def test_base_relationship_type_resolver_returns_none_without_type():
    class DummyRelationship:
        pass

    assert FirstModel._typedal_resolve_relationship_python_type(DummyRelationship()) is None


def example_typescript_renderer(context: RenderContext) -> str:
    db: TypeDAL = context.db_new

    return db.as_typescript(*context.tables)


def test_typescript_from_code_string():
    output = io.StringIO()
    success = render_schema_from_code(
        textwrap.dedent("""
        @db.define()
        class MyTable(TypedTable):
            key: str
            value: int

            second = relationship("SecondTable")

        @db.define()
        class SecondTable(TypedTable):
            key: str
            value: int
            secret = TypedField(str, readable=False)

        @db.define()
        class Unrelated(TypedTable):
            something: str

        """),
        output_file=output,
        renderer=example_typescript_renderer,
        magic=True,
        use_typedal=True,
        tables=["my_table"],
    )

    assert success

    output.seek(0)
    result = output.read()

    assert "interface MyTable {" in result
    assert "interface SecondTable {" in result
    assert "interface Unrelated {" not in result
