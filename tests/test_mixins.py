import time
import uuid
from datetime import datetime
from typing import Optional

import pytest

from src.typedal import TypeDAL, TypedTable
from src.typedal.fields import StringField, TypedField, UUIDField
from src.typedal.mixins import Mixin, SlugMixin, TimestampsMixin


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
