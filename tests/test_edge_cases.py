"""
Small behaviours that are easy to miss: mostly the 'other' side of a condition, kept here so branch coverage stays honest.
"""

import contextlib
import typing as t
import warnings
from decimal import Decimal
from pathlib import Path

import pytest
from pydal import Field
from pydal.validators import IS_NOT_EMPTY

from src.typedal import TypeDAL, TypedField, TypedTable, relationship
from src.typedal.extensions import _coerce_decimal, install_decimal_guard
from src.typedal.helpers import all_annotations, sql_expression
from src.typedal.mixins import HAS_UNIQUE_SLUG, SlugMixin
from src.typedal.updates import SQLUpdateDialect


class EdgeAuthor(TypedTable):
    name: str
    books = relationship(list["EdgeBook"], lambda author, book: author.id == book.author, lazy="allow")


class EdgeBook(TypedTable):
    author: EdgeAuthor
    title: str


class EdgeArticle(TypedTable, SlugMixin, slug_field="title"):
    title = TypedField(str, requires=[IS_NOT_EMPTY()])


class EdgeArticleCopy(TypedTable):
    title: str


@pytest.fixture
def db() -> t.Iterator[TypeDAL]:
    database = TypeDAL("sqlite:memory", enable_typedal_caching=False)
    database.define(EdgeAuthor)
    database.define(EdgeBook)
    database.define(EdgeArticle)
    try:
        yield database
    finally:
        database.close()


def test_folder_is_optional() -> None:
    with contextlib.closing(TypeDAL("sqlite:memory", folder="", enable_typedal_caching=False)) as database:
        assert database._adapter is not None


def test_all_annotations_can_exclude_names() -> None:
    annotations = all_annotations(EdgeBook, _except={"title"})
    assert "author" in annotations
    assert "title" not in annotations


def test_sql_expression_renders_none_as_null(db: TypeDAL) -> None:
    assert "NULL" in str(sql_expression(db, "title IS %s", None))


@pytest.mark.parametrize("value", [None, 1, 1.25, Decimal("2.50"), " 2.50 "])
def test_decimal_coercion_preserves_null_and_finite_values(value: t.Any) -> None:
    expected = None if value is None else Decimal(str(value).strip())
    assert _coerce_decimal(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        Decimal("NaN"),
        Decimal("sNaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
        float("nan"),
        float("inf"),
        float("-inf"),
        "NaN",
        "Infinity",
        "invalid",
    ],
)
def test_decimal_coercion_rejects_nonfinite_and_invalid_values(value: t.Any) -> None:
    with pytest.raises(ValueError, match="Invalid decimal"):
        _coerce_decimal(value)


def test_decimal_coercion_passes_empty_values_to_pydal(db: TypeDAL) -> None:
    assert _coerce_decimal(None) is None
    assert _coerce_decimal("") == ""

    # PyDAL renders "" as NULL for decimal fields, as it did before the guard existed:
    table = db.define_table("decimal_empty", Field("price", "decimal(10,2)"))
    row_id = table.insert(price="")
    assert table[row_id].price is None
    assert db(table.price == "").count() == 0


def test_decimal_guard_is_installed_once_and_skips_adapters_without_representer(db: TypeDAL) -> None:
    representer = db._adapter.representer
    guarded = representer.represent
    install_decimal_guard(db._adapter)
    assert representer.represent is guarded

    class NoRepresenter:
        pass

    install_decimal_guard(NoRepresenter())  # nothing to patch, no error


def test_unsupported_update_returning_dialect_raises(db: TypeDAL) -> None:
    with pytest.raises(NotImplementedError, match="UPDATE RETURNING"):
        SQLUpdateDialect(db._adapter.dialect).update_returning("UPDATE x SET y = 1;", "id")


def test_where_ignores_lambda_returning_nothing(db: TypeDAL) -> None:
    EdgeAuthor.insert(name="a")
    EdgeAuthor.insert(name="b")
    assert EdgeAuthor.where(lambda _: None).count() == 2


def test_bare_table_in_join_counts_as_explicit(db: TypeDAL) -> None:
    sql = EdgeAuthor.select(EdgeAuthor.name, EdgeBook.title, join=db.edge_book).to_sql()
    assert "edge_book" in sql


def test_collect_into_keeps_explicit_selection(db: TypeDAL) -> None:
    EdgeArticle.insert(title="Hello")
    rows = EdgeArticle.select(EdgeArticle.id, EdgeArticle.title).collect_into(EdgeArticleCopy)
    assert [row.title for row in rows] == ["Hello"]


class EdgeAsyncRow(TypedTable):
    name: str


@pytest.mark.asyncio
async def test_async_iteration_without_window_yields_all_rows(tmp_path: Path) -> None:
    # a file database: async workers use their own connection, which sqlite:memory can't share
    database = TypeDAL(f"sqlite://{tmp_path / 'async.db'}", folder=str(tmp_path), enable_typedal_caching=False)
    try:
        database.define(EdgeAsyncRow)
        EdgeAsyncRow.insert(name="a")
        EdgeAsyncRow.insert(name="b")
        database.commit()
        builder = EdgeAsyncRow.where(EdgeAsyncRow.id > 0).orderby(EdgeAsyncRow.id)
        assert [row.name async for row in builder] == ["a", "b"]
    finally:
        database.close()


def test_find_with_limitby_skips_leading_matches(db: TypeDAL) -> None:
    for name in "abc":
        EdgeAuthor.insert(name=name)
    rows = EdgeAuthor.collect()
    assert [row.name for row in rows.find(lambda _: True, limitby=(1, 3))] == ["b", "c"]


def test_hooks_are_registered_once(db: TypeDAL) -> None:
    def hook(*_args: t.Any) -> None: ...

    for register, hooks in [
        (EdgeBook.after_update, EdgeBook._after_update),
        (EdgeBook.before_delete, EdgeBook._before_delete),
        (EdgeBook.after_delete, EdgeBook._after_delete),
    ]:
        register(hook)
        register(hook)
        assert hooks.count(hook) == 1
        hooks.remove(hook)


def test_render_with_explicit_fields_skips_other_tables(db: TypeDAL) -> None:
    author = EdgeAuthor.insert(name="a")
    rendered = author.render(fields=[db.edge_author.name, db.edge_book.title])
    assert rendered.id == author.id
    assert rendered is not author


def test_lazy_allow_loads_without_warning(db: TypeDAL) -> None:
    author = EdgeAuthor.insert(name="a")
    EdgeBook.insert(author=author, title="t")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        books = EdgeAuthor(author.id).books
    assert [book.title for book in books] == ["t"]


def test_slug_validator_without_record_id(db: TypeDAL) -> None:
    validator = HAS_UNIQUE_SLUG(db, "edge_article.slug")
    assert validator.validate("Brand new") == "Brand new"


def test_slug_validator_excludes_the_record_being_edited(db: TypeDAL) -> None:
    from pydal.validators import ValidationError

    article = EdgeArticle.insert(title="Taken")
    validator = HAS_UNIQUE_SLUG(db, "edge_article.slug")
    with pytest.raises(ValidationError):
        validator.validate("Taken")
    assert validator.validate("Taken", record_id=article.id) == "Taken"


def test_slug_mixin_keeps_existing_requires_list(db: TypeDAL) -> None:
    requires = db.edge_article.title.requires
    assert isinstance(requires, list)
    assert any(isinstance(validator, HAS_UNIQUE_SLUG) for validator in requires)


def test_from_slug_without_join(db: TypeDAL) -> None:
    article = EdgeArticle.insert(title="Hello World")
    assert EdgeArticle.from_slug("hello-world", join=False).id == article.id
    assert EdgeArticle.from_slug_or_fail("hello-world", join=False).id == article.id
