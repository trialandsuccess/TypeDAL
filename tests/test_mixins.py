import time
from typing import Optional

import pydantic
import pytest

from src.typedal import TypeDAL, TypedTable
from src.typedal.mixins import Mixin, PydanticMixin, SlugMixin, TimestampsMixin
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


@pytest.fixture
def pydantic_db():
    db = TypeDAL("sqlite:memory")
    db.define(PydanticAuthor)
    db.define(PydanticBook)
    db.define(PydanticReview)
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
    result = ta.validate_python(  # fixme: should we test this? This seems more like testing pydantic itself
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
