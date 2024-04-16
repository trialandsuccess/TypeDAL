import time
from datetime import datetime
from typing import Optional

import pytest

from src.typedal import TypedTable, TypeDAL
from src.typedal.mixins import SlugMixin, TimestampsMixin


class TableWithMixins(TypedTable, SlugMixin, slug_field="name", slug_suffix=1):
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


def test_timestamps(db):
    row = TableWithTimestamps.insert(unrelated="Hi")
    db.commit()
    assert row.updated_at == row.created_at

    time.sleep(1)  # make sure datetimes are not equal

    row.update_record(unrelated="Bye")

    updated_row = TableWithTimestamps(id=row.id)

    assert updated_row.updated_at > updated_row.created_at
