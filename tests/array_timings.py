"""
Compare native Postgres arrays (ArrayField) with pydal's |a|b| lists (ListStringField/ListIntegerField).

Usage: python -m tests.array_timings postgres://user:pass@localhost:5432/db [rows] [items_per_row]
"""

import sys
import tempfile
import time
import typing as t

from src.typedal import TypeDAL, TypedTable
from src.typedal.fields import ArrayField, ListIntegerField, ListStringField


class BarLists(TypedTable):
    tags = ListStringField()
    nums = ListIntegerField()


class NativeArrays(TypedTable):
    tags = ArrayField(str)
    nums = ArrayField(int)


def _timed(label: str, func: t.Callable[[], t.Any]) -> float:
    start = time.perf_counter()
    func()
    elapsed = time.perf_counter() - start
    print(f"  {label:<34} {elapsed * 1000:9.1f} ms")
    return elapsed


def run(table: type[TypedTable], db: TypeDAL, rows: int, items: int) -> dict[str, float]:
    print(table.__name__)
    data = [
        {"tags": [f"tag {i}, item {j}" for j in range(items)], "nums": list(range(i, i + items))} for i in range(rows)
    ]
    results = {
        "insert": _timed(f"insert {rows} rows", lambda: [table.insert(**row) for row in data]),
        "bulk_insert": _timed(f"bulk_insert {rows} rows", lambda: table.bulk_insert(data)),
    }
    db.commit()
    results["select"] = _timed(f"select all {rows * 2} rows", lambda: table.collect())
    results["contains_str"] = _timed(
        "contains (str) x 50", lambda: [table.where(table.tags.contains(f"tag {i}, item 0")).count() for i in range(50)]
    )
    results["contains_str_cs"] = _timed(
        "contains (str, case-sensitive) x 50",
        lambda: [table.where(table.tags.contains(f"tag {i}, item 0", case_sensitive=True)).count() for i in range(50)],
    )
    results["contains_int"] = _timed(
        "contains (int) x 50", lambda: [table.where(table.nums.contains(i)).count() for i in range(50)]
    )
    results["update"] = _timed("update all rows", lambda: table.where(table.id > 0).update(tags=["x", "y"], nums=[1]))
    db.commit()
    return results


def main(uri: str, rows: int = 2000, items: int = 10) -> None:
    db = TypeDAL(uri, folder=tempfile.mkdtemp(), enable_typedal_caching=False)
    db.define(BarLists)
    db.define(NativeArrays)
    try:
        bar = run(BarLists, db, rows, items)
        native = run(NativeArrays, db, rows, items)
        print("native / bar (below 1 means native is faster)")
        for key, value in bar.items():
            print(f"  {key:<34} {native[key] / value:9.2f}")
    finally:
        db.rollback()
        BarLists.drop()
        NativeArrays.drop()
        db.commit()
        db.close()


if __name__ == "__main__":
    main(sys.argv[1], *(int(arg) for arg in sys.argv[2:]))
