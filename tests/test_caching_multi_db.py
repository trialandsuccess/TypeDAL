"""
Two caching-enabled databases in one process must each keep their own cache (issue #21).
"""

import contextlib

import pytest

from src.typedal import TypeDAL, TypedTable
from src.typedal.caching import _TypedalCache, cache_models, clear_cache, remove_cache_for_table


# module level, because the cache stores rows with `dill`:
class MultiDbAppItem(TypedTable):
    name: str


class MultiDbReportItem(TypedTable):
    name: str


@pytest.fixture
def two_dbs(tmp_path):
    app = TypeDAL(f"sqlite://{tmp_path / 'app.db'}", folder=str(tmp_path))
    report = TypeDAL(f"sqlite://{tmp_path / 'report.db'}", folder=str(tmp_path))
    app.define(MultiDbAppItem)
    report.define(MultiDbReportItem)
    yield app, report
    report.close()
    app.close()


def test_cache_models_are_per_database(two_dbs):
    app, report = two_dbs
    app_cache, app_dependency = cache_models(app)
    report_cache, report_dependency = cache_models(report)

    assert app_cache is not report_cache
    assert app_dependency is not report_dependency
    assert app_cache._db is app
    assert report_cache._db is report
    # the module-level base class is never bound by enabling caching:
    assert _TypedalCache._db is None
    assert str(app_cache._table) == str(report_cache._table) == "typedal_cache"


def test_cache_dependency_join_uses_its_database_cache(two_dbs):
    app, report = two_dbs
    MultiDbAppItem.insert(name="a")
    MultiDbReportItem.insert(name="r")
    MultiDbAppItem.cache().collect()
    MultiDbReportItem.cache().collect()

    for db in (app, report):
        cache, dependency = cache_models(db)
        entry = cache.collect().first()
        rows = dependency.join("entry").collect()

        assert len(rows) == 1
        joined_entry = rows.first().entry
        assert isinstance(joined_entry, cache)
        assert joined_entry._db is db
        assert joined_entry.id == entry.id
        assert joined_entry.key == entry.key
        assert joined_entry.data == entry.data


def test_cache_rows_land_in_the_queried_database(two_dbs):
    app, report = two_dbs
    app_cache, _ = cache_models(app)
    report_cache, _ = cache_models(report)

    MultiDbAppItem.insert(name="a")
    assert MultiDbAppItem.cache().collect().metadata["cache"]["status"] == "fresh"
    assert MultiDbAppItem.cache().collect().metadata["cache"]["status"] == "cached"

    assert app_cache.count() == 1
    assert report_cache.count() == 0

    # invalidation through the insert hook touches only the app database:
    MultiDbReportItem.insert(name="r")
    MultiDbReportItem.cache().collect()
    assert report_cache.count() == 1

    MultiDbAppItem.insert(name="b")
    assert app_cache.count() == 0
    assert report_cache.count() == 1

    clear_cache(report)
    assert report_cache.count() == 0


def test_closing_one_database_keeps_caching_working_on_another(two_dbs, tmp_path):
    app, _report = two_dbs

    other = TypeDAL(f"sqlite://{tmp_path / 'other.db'}", folder=str(tmp_path))
    other.close()

    # used to fail in the after-insert hook: "@define or db.define is not called on this class yet!"
    MultiDbAppItem.insert(name="after close")
    MultiDbAppItem.cache().collect()
    assert cache_models(app)[0].count() == 1


def test_cache_models_without_caching(tmp_path):
    db = TypeDAL("sqlite:memory", enable_typedal_caching=False, folder=str(tmp_path))
    try:
        with pytest.raises(RuntimeError):
            cache_models(db)
    finally:
        db.close()


def test_remove_cache_for_model_keeps_other_database_cache(two_dbs):
    app, report = two_dbs
    MultiDbAppItem.insert(name="a")
    MultiDbReportItem.insert(name="r")
    MultiDbAppItem.cache().collect()
    MultiDbReportItem.cache().collect()
    assert cache_models(app)[0].count() == 1
    assert cache_models(report)[0].count() == 1

    remove_cache_for_table(app, MultiDbAppItem)

    assert cache_models(app)[0].count() == 0
    assert MultiDbAppItem.cache().collect().metadata["cache"]["status"] == "fresh"
    assert cache_models(report)[0].count() == 1
    assert MultiDbReportItem.cache().collect().metadata["cache"]["status"] == "cached"


def test_closing_old_database_preserves_rebound_model(tmp_path):
    class SharedItem(TypedTable):
        name: str

    with contextlib.closing(TypeDAL("sqlite:memory", folder=str(tmp_path))) as report:
        with contextlib.closing(TypeDAL("sqlite:memory", folder=str(tmp_path))) as app:
            app.define(SharedItem)
            report.define(SharedItem)
            assert SharedItem._db is report

        assert SharedItem._db is report
        assert SharedItem._table is report.shared_item
        SharedItem.insert(name="after close")
        assert SharedItem.collect().first().name == "after close"

    assert SharedItem._db is None
    assert SharedItem._table is None
