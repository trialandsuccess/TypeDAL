import time
import typing
from typing import Optional

import pydantic
import pytest

from src.typedal import TypeDAL, TypedField, TypedTable
from src.typedal.mixins import BaseModeProtocol, Mixin, PydanticMixin, SlugMixin, TimestampsMixin, dump_pydantic
from src.typedal.relationships import relationship


class AllMixins(TypedTable, SlugMixin, TimestampsMixin, slug_field="name"):
    name: str


class TableWithMixins(TypedTable, SlugMixin, slug_field="name", slug_suffix_length=0):
    name: str
    number: Optional[int]


class TableWithSlugSuffix(TypedTable, SlugMixin, slug_field="name", slug_suffix_length=1):
    name: str
    number: Optional[int]


with pytest.warns(DeprecationWarning):

    class TableWithMixinsWarns(TypedTable, SlugMixin, slug_field="name", slug_suffix=1):
        name: str
        number: Optional[int]


class TableWithTimestamps(TypedTable, TimestampsMixin):
    unrelated: str


def test_invalid_slug_initialization():
    with pytest.raises(ValueError):

        class WithoutSlugField(TypedTable, SlugMixin):  # no slug_field=...
            ...


@pytest.fixture
def db():
    _db = TypeDAL("sqlite:memory")

    _db.define(AllMixins)
    _db.define(TableWithMixins)
    _db.define(TableWithSlugSuffix)
    _db.define(TableWithTimestamps)
    yield _db


def test_order(db):
    assert TableWithMixins.fields == [
        "id",  # id should always be the first (pydal itself already does this);
        "name",  # table own fields should come afterwards,
        "number",  # in the order they are defined;
        "slug",  # mixins should come last.
    ]


def test_slug(db):
    row, error = TableWithMixins.validate_and_insert(name="")
    assert row is None
    assert error

    # without random suffix: duplicates are forbidden

    row, error = TableWithMixins.validate_and_insert(name="Two Words")
    assert error is None

    assert row.name == "Two Words"
    assert row.slug == "two-words"

    assert TableWithMixins.from_slug(row.slug)
    assert TableWithMixins.from_slug("missing") is None

    assert TableWithMixins.from_slug_or_fail(row.slug)

    with pytest.raises(ValueError):
        TableWithMixins.from_slug_or_fail("missing")

    row, error = TableWithMixins.validate_and_insert(name="Two Words")
    assert row is None
    assert error == {
        "name": "This slug is not unique: two-words.",
    }

    # with random suffix: duplicates are fine

    row, error = TableWithSlugSuffix.validate_and_insert(name="Two Words")
    assert error is None

    assert row.name == "Two Words"
    assert row.slug.startswith("two-words-")

    row, error = TableWithSlugSuffix.validate_and_insert(name="Two Words")
    assert error is None

    assert row.name == "Two Words"
    assert row.slug.startswith("two-words")

    # test manual slug:
    manual_slug = "some-other-slug-manually-defined"
    # validate_and_insert will fail because 'slug' is not writable
    row = TableWithMixins.insert(name="Some Name", slug=manual_slug)

    assert row.slug == manual_slug


def test_timestamps(db):
    row = TableWithTimestamps.insert(unrelated="Hi")
    db.commit()
    assert row.updated_at == row.created_at

    time.sleep(1)  # make sure datetimes are not equal

    row.update_record(unrelated="Bye")

    updated_row = TableWithTimestamps(id=row.id)

    assert updated_row.updated_at > updated_row.created_at


def test_reusing(db):
    assert str(AllMixins.created_at) == "all_mixins.created_at"
    assert str(AllMixins.slug) == "all_mixins.slug"
    assert str(AllMixins.name) == "all_mixins.name"

    assert str(TableWithMixins.slug) == "table_with_mixins.slug"
    assert str(TableWithMixins.name) == "table_with_mixins.name"

    assert str(TableWithTimestamps.created_at) == "table_with_timestamps.created_at"
    assert str(TableWithTimestamps.unrelated) == "table_with_timestamps.unrelated"


def test_combining_mixins():
    class FirstMixin(Mixin):
        def __init_subclass__(cls, first: str, **kw):
            super().__init_subclass__(**kw)

            cls.__settings__["first"] = first

        @classmethod
        def one(cls):
            return cls.__settings__["first"] == "first" and cls.__settings__["second"] == "second"

    class SecondMixin(Mixin):
        def __init_subclass__(cls, second: str, **kw):
            super().__init_subclass__(**kw)

            cls.__settings__["second"] = second

        @classmethod
        def two(cls):
            return cls.__settings__["first"] == "first" and cls.__settings__["second"] == "second"

    class Combined(TypedTable, FirstMixin, SecondMixin, first="first", second="second"): ...

    assert Combined.one()
    assert Combined.two()

    class CombinedDifferentOrder(TypedTable, SecondMixin, FirstMixin, first="first", second="second"): ...

    assert CombinedDifferentOrder.one()
    assert CombinedDifferentOrder.two()


# ──────────────────────────────────────────────────────────────
# PydanticMixin tests
# ──────────────────────────────────────────────────────────────


class PydanticAuthor(TypedTable, PydanticMixin):
    name: str


class PydanticBook(TypedTable, PydanticMixin):
    title: str
    author: PydanticAuthor

    # forward-reference relationship resolved at runtime
    reviews = relationship(list["PydanticReview"], lambda self, other: other.book == self.id)

    @property
    def title_upper(self) -> str:
        return self.title.upper()


class PydanticReview(TypedTable, PydanticMixin):
    body: str
    book: PydanticBook


class NonPydanticAuthor(TypedTable):
    name: str


class PydanticStringRelationship(TypedTable, PydanticMixin):
    name: str
    author_link = relationship("PydanticAuthor", lambda self, other: self.id == other.id)


class PydanticGenericResolvedRelationship(TypedTable, PydanticMixin):
    name: str
    weird = relationship(tuple[PydanticAuthor, int], lambda self, other: self.id == other.id)


class PydanticGenericUnresolvedRelationship(TypedTable, PydanticMixin):
    name: str
    weird = relationship(tuple["MissingModel", int], lambda self, other: self.id == other.id)


class PydanticSchemaShapes(TypedTable, PydanticMixin):
    typed_counter = TypedField(int, default=0)
    mapping_payload: typing.Mapping[str, int]
    maybe_count: int | None
    int_or_text: int | str
    fixed_pair: tuple[int, int]


class PydanticNoGetterProperty(TypedTable, PydanticMixin):
    name: str
    empty_prop = property()


class PydanticTupleAndLiteralSchema(TypedTable, PydanticMixin):
    numbers: tuple[int, ...]
    status: typing.Literal["draft", "published"]


class PydanticHiddenTypedField(TypedTable, PydanticMixin):
    visible = TypedField(str)
    hidden = TypedField(str, readable=False)


class PydanticHiddenRelationshipHost(TypedTable, PydanticMixin):
    name: str
    with_hidden = relationship(list[PydanticHiddenTypedField], lambda self, other: self.id == other.id, lazy="allow")


@pytest.fixture
def pydantic_db():
    db = TypeDAL("sqlite:memory")
    db.define(PydanticAuthor)
    db.define(PydanticBook)
    db.define(PydanticReview)
    db.define(PydanticStringRelationship)
    db.define(PydanticGenericResolvedRelationship)
    db.define(PydanticGenericUnresolvedRelationship)
    db.define(PydanticHiddenTypedField)
    db.define(PydanticHiddenRelationshipHost)
    db.define(NonPydanticAuthor)
    yield db


def test_pydantic_model_dump(pydantic_db):
    author = PydanticAuthor.insert(name="Alice")
    data = author.model_dump()
    assert data == {"id": author.id, "name": "Alice"}


def test_pydantic_model_dump_json_mode(pydantic_db):
    author = PydanticAuthor.insert(name="Alice")
    data = author.model_dump(mode="json")
    assert data == {"id": author.id, "name": "Alice"}


def test_pydantic_fields_basic(pydantic_db):
    fields = PydanticAuthor._pydantic_fields()
    assert "id" in fields
    assert "name" in fields


def test_pydantic_fields_with_relationships(pydantic_db):
    fields_without = PydanticBook._pydantic_fields(include_relationships=False)
    fields_with = PydanticBook._pydantic_fields(include_relationships=True)

    assert "reviews" not in fields_without
    assert "reviews" in fields_with


def test_pydantic_fields_with_properties(pydantic_db):
    fields_without = PydanticBook._pydantic_fields(include_properties=False)
    fields_with = PydanticBook._pydantic_fields(include_properties=True)

    assert "title_upper" not in fields_without
    assert "title_upper" in fields_with
    assert fields_with["title_upper"] == str


def test_pydantic_type_adapter_validate(pydantic_db):
    ta = pydantic.TypeAdapter(PydanticAuthor)
    result = ta.validate_python({"id": 1, "name": "Alice"})
    assert result == {"id": 1, "name": "Alice"}


def test_pydantic_type_adapter_json_schema(pydantic_db):
    ta = pydantic.TypeAdapter(PydanticAuthor)
    schema = ta.json_schema()
    assert schema["type"] == "object"
    props = schema["properties"]
    assert "id" in props
    assert "name" in props


def test_pydantic_nested_model(pydantic_db):
    author = PydanticAuthor.insert(name="Alice")
    book = PydanticBook.insert(title="My Book", author=author.id)

    # model_dump on the book should include the author FK as int
    data = book.model_dump()
    assert data["title"] == "My Book"
    # author is stored as an FK int
    assert data["author"] == author.id


def test_pydantic_model_dump_with_join(pydantic_db):
    author = PydanticAuthor.insert(name="Alice")
    book = PydanticBook.insert(title="My Book", author=author.id)
    PydanticReview.insert(body="Great!", book=book.id)

    joined = PydanticBook.where(id=book.id).join("reviews").first()
    assert joined is not None

    data = joined.model_dump()
    assert data["title"] == "My Book"
    assert isinstance(data["reviews"], list)
    assert len(data["reviews"]) == 1
    assert data["reviews"][0]["body"] == "Great!"


def test_pydantic_json_mode_nested(pydantic_db):
    author = PydanticAuthor.insert(name="Alice")
    book = PydanticBook.insert(title="My Book", author=author.id)
    PydanticReview.insert(body="Great!", book=book.id)

    joined = PydanticBook.where(id=book.id).join("reviews").first()
    assert joined is not None

    data = joined.model_dump(mode="json")
    assert isinstance(data, dict)
    assert data["reviews"][0]["body"] == "Great!"


def test_pydantic_incompatible_reference_raises():
    with pytest.raises(ValueError, match="not Pydantic-compatible"):

        class BadBook(TypedTable, PydanticMixin):
            title: str
            author: NonPydanticAuthor

        db = TypeDAL("sqlite:memory")
        db.define(NonPydanticAuthor)
        db.define(BadBook)
        BadBook._pydantic_fields()


def test_pydantic_type_adapter_with_relationships(pydantic_db):
    author = PydanticAuthor.insert(name="Alice")
    book = PydanticBook.insert(title="My Book", author=author.id)
    PydanticReview.insert(body="Excellent", book=book.id)

    joined = PydanticBook.where(id=book.id).join("reviews").first()
    assert joined is not None

    ta = pydantic.TypeAdapter(PydanticBook)
    # validate a plain dict that includes a nested review list;
    # computed properties (title_upper) are required in the schema so must be supplied here
    result = ta.validate_python(
        {
            "id": book.id,
            "title": "My Book",
            "title_upper": "MY BOOK",
            "author": author.id,
            "reviews": [{"id": 1, "body": "Excellent", "book": book.id}],
        }
    )
    assert result["title"] == "My Book"
    assert result["title_upper"] == "MY BOOK"
    assert result["reviews"][0]["body"] == "Excellent"


def test_pydantic_string_relationship_resolves_via_namespace(pydantic_db):
    fields = PydanticStringRelationship._pydantic_fields(include_relationships=True)
    assert fields["author_link"] is PydanticAuthor


def test_pydantic_generic_relationship_resolution_behavior(pydantic_db):
    resolved_fields = PydanticGenericResolvedRelationship._pydantic_fields(include_relationships=True)
    assert resolved_fields["weird"] == tuple[PydanticAuthor, int]

    unresolved_fields = PydanticGenericUnresolvedRelationship._pydantic_fields(include_relationships=True)
    assert "weird" not in unresolved_fields


def test_dump_pydantic_supports_basemodel_and_row_shapes():
    class Payload(pydantic.BaseModel):
        title: str

    class AsDict:
        def as_dict(self):
            return {"nested": Payload(title="ok")}

    class AsList:
        def as_list(self):
            return [Payload(title="ok")]

    assert dump_pydantic(Payload(title="x")) == {"title": "x"}
    assert dump_pydantic(AsDict()) == {"nested": {"title": "ok"}}
    assert dump_pydantic(AsList()) == [{"title": "ok"}]


def test_pydantic_converter_handles_primitives_and_objects():
    converter = PydanticMixin._make_instance_converter(PydanticAuthor, {"id": int, "name": str})

    assert converter(12) == {"id": 12}
    assert converter("text") == "text"
    assert converter(None) is None

    row_like = type("RowLike", (), {"id": 1, "name": "Alice"})()
    assert converter(row_like) == {"id": 1, "name": "Alice"}


def test_pydantic_field_schema_covers_mapping_union_and_fallback(pydantic_db):
    schema = pydantic.TypeAdapter(PydanticSchemaShapes).json_schema()
    props = schema["properties"]

    assert any(option.get("type") == "object" for option in props["mapping_payload"]["anyOf"])
    assert props["maybe_count"]["anyOf"]
    assert props["int_or_text"]["anyOf"]
    tuple_option = next(option for option in props["fixed_pair"]["anyOf"] if option.get("type") == "array")
    assert tuple_option["prefixItems"]


def test_pydantic_field_schema_covers_variadic_tuple_and_literal_fallback():
    schema = pydantic.TypeAdapter(PydanticTupleAndLiteralSchema).json_schema()
    props = schema["properties"]

    numbers_option = next(option for option in props["numbers"]["anyOf"] if option.get("type") == "array")
    assert numbers_option["items"]["type"] == "integer"

    status_option = next(option for option in props["status"]["anyOf"] if "enum" in option)
    assert set(status_option["enum"]) == {"draft", "published"}


def test_pydantic_fields_include_typedfield_and_skip_no_getter_property(pydantic_db):
    fields = PydanticSchemaShapes._pydantic_fields()
    assert fields["typed_counter"] is int

    property_fields = PydanticNoGetterProperty._pydantic_fields(include_properties=True)
    assert "empty_prop" not in property_fields


def test_pydantic_skips_unreadable_typedfield_in_model_dump(pydantic_db):
    row = PydanticHiddenTypedField.insert(visible="show", hidden="hide")
    data = row.model_dump()
    assert data == {"id": row.id, "visible": "show"}

    PydanticHiddenTypedField.visible.readable = False
    row = PydanticHiddenTypedField.insert(visible="show-2", hidden="hide-2")
    data = row.model_dump()
    assert data == {"id": row.id}


def test_pydantic_skips_unreadable_typedfield_in_nested_list_relationship_dump(pydantic_db):
    hidden = PydanticHiddenTypedField.insert(visible="show", hidden="hide")
    host = PydanticHiddenRelationshipHost.insert(name="Host")
    assert hidden.id == host.id

    joined = PydanticHiddenRelationshipHost.where(id=host.id).join("with_hidden").first()
    assert joined is not None

    data = joined.model_dump(mode="json")
    assert data["with_hidden"] == [{"id": hidden.id, "visible": "show"}]
    assert joined.with_hidden[0].hidden == "hide"
    assert [item.model_dump() for item in joined.with_hidden] == [{"id": hidden.id, "visible": "show"}]


def test_pydantic_model_dump_never_lazy_loads_unjoined_relationships(pydantic_db):
    hidden = PydanticHiddenTypedField.insert(visible="show", hidden="hide")
    host = PydanticHiddenRelationshipHost.insert(name="Host")
    assert hidden.id == host.id

    data = host.model_dump(mode="json")
    assert "with_hidden" not in data


def test_pydantic_type_adapter_skips_unreadable_fields(pydantic_db):
    row = PydanticHiddenTypedField.insert(visible="show", hidden="hide")
    ta = pydantic.TypeAdapter(PydanticHiddenTypedField)
    data = ta.validate_python(row)
    assert data == {"id": row.id, "visible": "show"}


def test_pydantic_type_adapter_never_lazy_loads_unjoined_relationships(pydantic_db):
    hidden = PydanticHiddenTypedField.insert(visible="show", hidden="hide")
    host = PydanticHiddenRelationshipHost.insert(name="Host")
    assert hidden.id == host.id

    ta = pydantic.TypeAdapter(PydanticHiddenRelationshipHost)
    data = ta.validate_python(host)
    assert "with_hidden" not in data


def test_pydantic_compatibility_non_type_and_missing_relationship_type():
    # ForwardRef is not a runtime type; this should be a no-op, not a crash.
    PydanticMixin._ensure_pydantic_compatible_type("x", typing.ForwardRef("Anything"))

    class DummyRelationship:
        pass

    assert PydanticMixin._typedal_resolve_relationship_python_type(DummyRelationship()) is None


def test_pydantic_relationship_resolution_without_db_context():
    class Detached(TypedTable, PydanticMixin):
        name: str
        rel = relationship("PydanticAuthor", lambda self, other: self.id == other.id)

    assert Detached._typedal_resolve_relationship_python_type(Detached.rel) is None


def test_pydantic_basemodel_matches_basemode_protocol():
    class Payload(pydantic.BaseModel):
        title: str

    assert isinstance(Payload(title="x"), BaseModeProtocol)
