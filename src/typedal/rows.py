"""
Contains base functionality related to Rows (raw result of a database query).
"""

from __future__ import annotations

import csv
import json
import typing as t

import pydal.objects

from .core import TypeDAL
from .helpers import mktable
from .query_builder import QueryBuilder
from .serializers import as_json
from .tables import TypedTable
from .types import (
    AnyDict,
    Field,
    Metadata,
    PaginateDict,
    Pagination,
    Query,
    Row,
    Rows,
    T,
    T_MetaInstance,
)


class TypedRows(t.Collection[T_MetaInstance], Rows):
    """
    Slighly enhaned and typed functionality on top of pydal Rows (the result of a select).
    """

    records: dict[int, T_MetaInstance]
    # _rows: Rows
    model: t.Type[T_MetaInstance]
    metadata: Metadata

    # pseudo-properties: actually stored in _rows
    db: TypeDAL
    colnames: list[str]
    fields: list[Field]
    colnames_fields: list[Field]
    response: list[tuple[t.Any, ...]]

    def __init__(
        self,
        rows: Rows,
        model: t.Type[T_MetaInstance],
        records: dict[int, T_MetaInstance] = None,
        metadata: Metadata = None,
        raw: dict[int, list[Row]] = None,
    ) -> None:
        """
        Should not be called manually!

        Normally, the `records` from an existing `Rows` object are used
            but these can be overwritten with a `records` dict.
        `metadata` can be t.Any (un)structured data
        `model` is a Typed Table class
        """

        def _get_id(row: Row) -> int:
            """
            Try to find the id field in a row.

            If _extra exists, the row changes:
            <Row {'test_relationship': {'id': 1}, '_extra': {'COUNT("test_relationship"."querytable")': 8}}>
            """
            if idx := getattr(row, "id", None):
                return t.cast(int, idx)
            elif main := getattr(row, str(model), None):
                return t.cast(int, main.id)
            else:  # pragma: no cover
                raise NotImplementedError(f"`id` could not be found for {row}")

        records = records or {_get_id(row): model(row) for row in rows}
        raw = raw or {}

        for idx, entity in records.items():
            entity._rows = tuple(raw.get(idx, []))

        super().__init__(rows.db, records, rows.colnames, rows.compact, rows.response, rows.fields)
        self.model = model
        self.metadata = metadata or {}
        self.colnames = rows.colnames

    def __len__(self) -> int:
        """
        Return the count of rows.
        """
        return len(self.records)

    def __iter__(self) -> t.Iterator[T_MetaInstance]:
        """
        Loop through the rows.
        """
        yield from self.records.values()

    def __contains__(self, ind: t.Any) -> bool:
        """
        Check if an id exists in this result set.
        """
        return ind in self.records

    def first(self) -> T_MetaInstance | None:
        """
        Get the row with the lowest id.
        """
        if not self.records:
            return None

        return next(iter(self))

    def last(self) -> T_MetaInstance | None:
        """
        Get the row with the highest id.
        """
        if not self.records:
            return None

        max_id = max(self.records.keys())
        return self[max_id]

    def find(
        self,
        f: t.Callable[[T_MetaInstance], Query],
        limitby: tuple[int, int] = None,
    ) -> "TypedRows[T_MetaInstance]":
        """
        Returns a new Rows object, a subset of the original object, filtered by the function `f`.
        """
        if not self.records:
            return self.__class__(self, self.model, {})

        records = {}
        if limitby:
            _min, _max = limitby
        else:
            _min, _max = 0, len(self)
        count = 0
        for i, row in self.records.items():
            if f(row):
                if _min <= count:
                    records[i] = row
                count += 1
                if count == _max:
                    break

        return self.__class__(self, self.model, records)

    def exclude(self, f: t.Callable[[T_MetaInstance], Query]) -> "TypedRows[T_MetaInstance]":
        """
        Removes elements from the calling Rows object, filtered by the function `f`, \
            and returns a new Rows object containing the removed elements.
        """
        if not self.records:
            return self.__class__(self, self.model, {})
        removed = {}
        to_remove = []
        for i in self.records:
            row = self[i]
            if f(row):
                removed[i] = self.records[i]
                to_remove.append(i)

        [self.records.pop(i) for i in to_remove]

        return self.__class__(
            self,
            self.model,
            removed,
        )

    def sort(self, f: t.Callable[[T_MetaInstance], t.Any], reverse: bool = False) -> list[T_MetaInstance]:
        """
        Returns a list of sorted elements (not sorted in place).
        """
        return [r for (r, s) in sorted(zip(self.records.values(), self), key=lambda r: f(r[1]), reverse=reverse)]

    def __str__(self) -> str:
        """
        Simple string representation.
        """
        return f"<TypedRows with {len(self)} records>"

    def __repr__(self) -> str:
        """
        Print a table on repr().
        """
        data = self.as_dict()
        try:
            headers = list(next(iter(data.values())).keys())
        except StopIteration:
            headers = []

        return mktable(data, headers)

    def group_by_value(
        self,
        *fields: "str | Field | TypedField[T]",
        one_result: bool = False,
        **kwargs: t.Any,
    ) -> dict[T, list[T_MetaInstance]]:
        """
        Group the rows by a specific field (which will be the dict key).
        """
        kwargs["one_result"] = one_result
        result = super().group_by_value(*fields, **kwargs)
        return t.cast(dict[T, list[T_MetaInstance]], result)

    def as_csv(self) -> str:
        """
        Dump the data to csv.
        """
        return t.cast(str, super().as_csv())

    def as_dict(
        self,
        key: str | Field | None = None,
        compact: bool = False,
        storage_to_dict: bool = False,
        datetime_to_str: bool = False,
        custom_types: list[type] | None = None,
    ) -> dict[int, AnyDict]:
        """
        Get the data in a dict of dicts.
        """
        if any([key, compact, storage_to_dict, datetime_to_str, custom_types]):
            # functionality not guaranteed
            if isinstance(key, Field):
                key = key.name

            return t.cast(
                dict[int, AnyDict],
                super().as_dict(
                    key or "id",
                    compact,
                    storage_to_dict,
                    datetime_to_str,
                    custom_types,
                ),
            )

        return {k: v.as_dict() for k, v in self.records.items()}

    def as_json(
        self, default: t.Callable[[t.Any], t.Any] = None, indent: t.Optional[int] = None, **kwargs: t.Any
    ) -> str:
        """
        Turn the data into a dict and then dump to JSON.
        """
        data = self.as_list()

        return as_json.encode(data, default=default, indent=indent, **kwargs)

    def json(self, default: t.Callable[[t.Any], t.Any] = None, indent: t.Optional[int] = None, **kwargs: t.Any) -> str:
        """
        Turn the data into a dict and then dump to JSON.
        """
        return self.as_json(default=default, indent=indent, **kwargs)

    def as_list(
        self,
        compact: bool = False,
        storage_to_dict: bool = False,
        datetime_to_str: bool = False,
        custom_types: list[type] = None,
    ) -> list[AnyDict]:
        """
        Get the data in a list of dicts.
        """
        if any([compact, storage_to_dict, datetime_to_str, custom_types]):
            return t.cast(list[AnyDict], super().as_list(compact, storage_to_dict, datetime_to_str, custom_types))

        return [_.as_dict() for _ in self.records.values()]

    def __getitem__(self, item: int) -> T_MetaInstance:
        """
        You can get a specific row by ID from a typedrows by using rows[idx] notation.

        Since pydal's implementation differs (they expect a list instead of a dict with id keys),
        using rows[0] will return the first row, regardless of its id.
        """
        try:
            return self.records[item]
        except KeyError as e:
            if item == 0 and (row := self.first()):
                # special case: pydal internals think Rows.records is a list, not a dict
                return row

            raise e

    def get(self, item: int) -> t.Optional[T_MetaInstance]:
        """
        Get a row by ID, or receive None if it isn't in this result set.
        """
        return self.records.get(item)

    def update(self, **new_values: t.Any) -> bool:
        """
        Update the current rows in the database with new_values.
        """
        # cast to make mypy understand .id is a TypedField and not an int!
        table = t.cast(t.Type[TypedTable], self.model._ensure_table_defined())

        ids = set(self.column("id"))
        query = table.id.belongs(ids)
        return bool(self.db(query).update(**new_values))

    def delete(self) -> bool:
        """
        Delete the currently selected rows from the database.
        """
        # cast to make mypy understand .id is a TypedField and not an int!
        table = t.cast(t.Type[TypedTable], self.model._ensure_table_defined())

        ids = set(self.column("id"))
        query = table.id.belongs(ids)
        return bool(self.db(query).delete())

    def join(
        self,
        field: "Field | TypedField[t.Any]",
        name: str = None,
        constraint: Query = None,
        fields: list[str | Field] = None,
        orderby: t.Optional[str | Field] = None,
    ) -> T_MetaInstance:
        """
        This can be used to JOIN with some relationships after the initial select.

        Using the querybuilder's .join() method is prefered!
        """
        result = super().join(field, name, constraint, fields or [], orderby)
        return t.cast(T_MetaInstance, result)

    def export_to_csv_file(
        self,
        ofile: t.TextIO,
        null: t.Any = "<NULL>",
        delimiter: str = ",",
        quotechar: str = '"',
        quoting: int = csv.QUOTE_MINIMAL,
        represent: bool = False,
        colnames: list[str] = None,
        write_colnames: bool = True,
        *args: t.Any,
        **kwargs: t.Any,
    ) -> None:
        """
        Shadow export_to_csv_file from Rows, but with typing.

        See http://web2py.com/books/default/chapter/29/06/the-database-abstraction-layer?search=export_to_csv_file#Exporting-and-importing-data
        """
        super().export_to_csv_file(
            ofile,
            null,
            *args,
            delimiter=delimiter,
            quotechar=quotechar,
            quoting=quoting,
            represent=represent,
            colnames=colnames or self.colnames,
            write_colnames=write_colnames,
            **kwargs,
        )

    @classmethod
    def from_rows(
        cls,
        rows: Rows,
        model: t.Type[T_MetaInstance],
        metadata: Metadata = None,
    ) -> "TypedRows[T_MetaInstance]":
        """
        Internal method to convert a Rows object to a TypedRows.
        """
        return cls(rows, model, metadata=metadata)

    def __getstate__(self) -> AnyDict:
        """
        Used by dill to dump to bytes (exclude db connection etc).
        """
        return {
            "metadata": json.dumps(self.metadata, default=str),
            "records": self.records,
            "model": str(self.model._table),
            "colnames": self.colnames,
        }

    def __setstate__(self, state: AnyDict) -> None:
        """
        Used by dill when loading from a bytestring.
        """
        state["metadata"] = json.loads(state["metadata"])
        self.__dict__.update(state)
        # db etc. set after undill by caching.py

    def render(
        self,
        i: int | None = None,
        fields: list[Field] | None = None,
    ) -> t.Generator[T_MetaInstance, None, None]:
        """
        Takes an index and returns a copy of the indexed row with values \
            transformed via the "represent" attributes of the associated fields.

        Args:
            i: index. If not specified, a generator is returned for iteration
                over all the rows.
            fields: a list of fields to transform (if None, all fields with
                "represent" attributes will be transformed)
        """
        if i is None:
            # difference: uses .keys() instead of index
            return (self.render(i, fields=fields) for i in self.records)

        if not self.db.has_representer("rows_render"):  # pragma: no cover
            raise RuntimeError(
                "Rows.render() needs a `rows_render` representer in DAL instance",
            )

        row = self.records[i]
        return row.render(fields, compact=self.compact)


class PaginatedRows(TypedRows[T_MetaInstance]):
    """
    Extension on top of rows that is used when calling .paginate() instead of .collect().
    """

    _query_builder: QueryBuilder[T_MetaInstance]

    @property
    def data(self) -> list[T_MetaInstance]:
        """
        Get the underlying data.
        """
        return list(self.records.values())

    @property
    def pagination(self) -> Pagination:
        """
        Get all page info.
        """
        pagination_data = self.metadata["pagination"]

        has_next_page = pagination_data["current_page"] < pagination_data["max_page"]
        has_prev_page = pagination_data["current_page"] > 1
        return {
            "total_items": pagination_data["rows"],
            "current_page": pagination_data["current_page"],
            "per_page": pagination_data["limit"],
            "total_pages": pagination_data["max_page"],
            "has_next_page": has_next_page,
            "has_prev_page": has_prev_page,
            "next_page": pagination_data["current_page"] + 1 if has_next_page else None,
            "prev_page": pagination_data["current_page"] - 1 if has_prev_page else None,
        }

    def next(self) -> t.Self:
        """
        Get the next page.
        """
        data = self.metadata["pagination"]
        if data["current_page"] >= data["max_page"]:
            raise StopIteration("Final Page")

        return self._query_builder.paginate(limit=data["limit"], page=data["current_page"] + 1)

    def previous(self) -> t.Self:
        """
        Get the previous page.
        """
        data = self.metadata["pagination"]
        if data["current_page"] <= 1:
            raise StopIteration("First Page")

        return self._query_builder.paginate(limit=data["limit"], page=data["current_page"] - 1)

    def as_dict(self, *_: t.Any, **__: t.Any) -> PaginateDict:  # type: ignore
        """
        Convert to a dictionary with pagination info and original data.

        All arguments are ignored!
        """
        return {"data": super().as_dict(), "pagination": self.pagination}


class TypedSet(pydal.objects.Set):  # type: ignore # pragma: no cover
    """
    Used to make pydal Set more typed.

    This class is not actually used, only 'cast' by TypeDAL.__call__
    """

    def count(self, distinct: t.Optional[bool] = None, cache: AnyDict = None) -> int:
        """
        Count returns an int.
        """
        result = super().count(distinct, cache)
        return t.cast(int, result)

    def select(self, *fields: t.Any, **attributes: t.Any) -> TypedRows[T_MetaInstance]:
        """
        Select returns a TypedRows of a user defined table.

        Example:
            result: TypedRows[MyTable] = db(MyTable.id > 0).select()

            for row in result:
                reveal_type(row)  # MyTable
        """
        rows = super().select(*fields, **attributes)
        return t.cast(TypedRows[T_MetaInstance], rows)


# note: these imports exist at the bottom of this file to prevent circular import issues:

from .fields import TypedField  # noqa: E402
