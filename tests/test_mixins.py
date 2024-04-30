import time
from datetime import datetime
from typing import Optional

import pytest

from src.typedal import TypedTable, TypeDAL
from src.typedal.mixins import SlugMixin, TimestampsMixin


class AllMixins(TypedTable, SlugMixin, TimestampsMixin, slug_field="name"):
    name: str


class TableWithMixins(TypedTable, SlugMixin, slug_field="name", slug_suffix_length=1):
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
    row = TableWithMixins.insert(
        name="Two Words"
    )

    assert row.name == "Two Words"
    assert row.slug.startswith("two-words")

    assert TableWithMixins.from_slug(row.slug)
    assert TableWithMixins.from_slug("missing") is None

    assert TableWithMixins.from_slug_or_fail(row.slug)

    with pytest.raises(ValueError):
        TableWithMixins.from_slug_or_fail("missing")


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
