"""
Seperates the table definition code from core DAL code.

Since otherwise helper methods would clutter up the TypeDAl class.
"""

from __future__ import annotations

import copy
import types
import typing as t
import warnings

import pydal

from .constants import BASIC_MAPPINGS
from .core import TypeDAL, evaluate_forward_reference, resolve_annotation
from .fields import TypedField, is_typed_field
from .helpers import (
    all_annotations,
    all_dict,
    filter_out,
    instanciate,
    is_union,
    origin_is_subclass,
    to_snake,
)
from .relationships import Relationship, to_relationship
from .tables import TypedTable
from .types import (
    Field,
    T,
    T_annotation,
    Table,
    _Types,
)

try:
    # python 3.14+
    from annotationlib import ForwardRef
except ImportError:  # pragma: no cover
    # python 3.13-
    from typing import ForwardRef


class TableDefinitionBuilder:
    """Handles the conversion of TypedTable classes to pydal tables."""

    def __init__(self, db: "TypeDAL"):
        """
        Before, the `class_map` was a singleton on the pydal class; now it's per database.
        """
        self.db = db
        self.class_map: dict[str, t.Type["TypedTable"]] = {}

    def define(self, cls: t.Type[T], **kwargs: t.Any) -> t.Type[T]:
        """Build and register a table from a TypedTable class."""
        full_dict = all_dict(cls)
        tablename = to_snake(cls.__name__)
        annotations = all_annotations(cls)
        annotations |= {k: t.cast(type, v) for k, v in full_dict.items() if is_typed_field(v)}
        annotations = {k: v for k, v in annotations.items() if not k.startswith("_")}

        typedfields: dict[str, TypedField[t.Any]] = {
            k: instanciate(v, True) for k, v in annotations.items() if is_typed_field(v)
        }

        relationships: dict[str, type[Relationship[t.Any]]] = filter_out(annotations, Relationship)
        fields = {fname: self.to_field(fname, ftype) for fname, ftype in annotations.items()}

        other_kwargs = kwargs | {
            k: v for k, v in cls.__dict__.items() if k not in annotations and not k.startswith("_")
        }

        for key, field in typedfields.items():
            clone = copy.copy(field)
            setattr(cls, key, clone)
            typedfields[key] = clone

        relationships = filter_out(full_dict, Relationship) | relationships | filter_out(other_kwargs, Relationship)

        reference_field_keys = [
            k for k, v in fields.items() if str(v.type).split(" ")[0] in ("list:reference", "reference")
        ]

        relationships |= {
            k: new_relationship
            for k in reference_field_keys
            if k not in relationships and (new_relationship := to_relationship(cls, k, annotations[k]))
        }

        cache_dependency = self.db._config.caching and kwargs.pop("cache_dependency", True)
        table: Table = self.db.define_table(tablename, *fields.values(), **kwargs)

        for name, typed_field in typedfields.items():
            field = fields[name]
            typed_field.bind(field, table)

        if issubclass(cls, TypedTable):
            cls.__set_internals__(
                db=self.db,
                table=table,
                relationships=t.cast(dict[str, Relationship[t.Any]], relationships),
            )
            self.class_map[str(table)] = cls
            self.class_map[table._rname] = cls
            cls.__on_define__(self.db)
        else:
            warnings.warn("db.define used without inheriting TypedTable. This could lead to strange problems!")

        if not tablename.startswith("typedal_") and cache_dependency:
            from .caching import _remove_cache

            table._before_update.append(lambda s, _: _remove_cache(s, tablename))
            table._before_delete.append(lambda s: _remove_cache(s, tablename))

        return cls

    def to_field(self, fname: str, ftype: type, **kw: t.Any) -> Field:
        """Convert annotation to pydal Field."""
        fname = to_snake(fname)
        if converted_type := self.annotation_to_pydal_fieldtype(ftype, kw):
            return self.build_field(fname, converted_type, **kw)
        else:
            raise NotImplementedError(f"Unsupported type {ftype}/{type(ftype)}")

    def annotation_to_pydal_fieldtype(
        self,
        ftype_annotation: T_annotation,
        mut_kw: t.MutableMapping[str, t.Any],
    ) -> t.Optional[str]:
        """Convert Python type annotation to pydal field type string."""
        ftype = t.cast(type, ftype_annotation)  # cast from Type to type to make mypy happy)

        if isinstance(ftype, str):
            # extract type from string
            ftype = resolve_annotation(ftype)

        if isinstance(ftype, ForwardRef):
            known_classes = {table.__name__: table for table in self.class_map.values()}

            ftype = evaluate_forward_reference(ftype, namespace=known_classes)

        if mapping := BASIC_MAPPINGS.get(ftype):
            # basi types
            return mapping
        elif isinstance(ftype, pydal.objects.Table):
            # db.table
            return f"reference {ftype._tablename}"
        elif issubclass(type(ftype), type) and issubclass(ftype, TypedTable):
            # SomeTable
            snakename = to_snake(ftype.__name__)
            return f"reference {snakename}"
        elif isinstance(ftype, TypedField):
            # FieldType(type, ...)
            return ftype._to_field(mut_kw, self)
        elif origin_is_subclass(ftype, TypedField):
            # TypedField[int]
            return self.annotation_to_pydal_fieldtype(t.get_args(ftype)[0], mut_kw)
        elif isinstance(ftype, types.GenericAlias) and t.get_origin(ftype) in (list, TypedField):  # type: ignore
            # list[str] -> str -> string -> list:string
            _child_type = t.get_args(ftype)[0]
            _child_type = self.annotation_to_pydal_fieldtype(_child_type, mut_kw)
            return f"list:{_child_type}"
        elif is_union(ftype):
            # str | int -> UnionType
            # typing.Union[str | int] -> typing._UnionGenericAlias

            # Optional[type] == type | None

            match t.get_args(ftype):
                case (_child_type, _Types.NONETYPE) | (_Types.NONETYPE, _child_type):
                    # good union of Nullable

                    # if a field is optional, it is nullable:
                    mut_kw["notnull"] = False
                    return self.annotation_to_pydal_fieldtype(_child_type, mut_kw)
                case _:
                    # two types is not supported by the db!
                    return None
        else:
            return None

    @classmethod
    def build_field(cls, name: str, field_type: str, **kw: t.Any) -> Field:
        """Create a pydal Field with default kwargs."""
        kw_combined = TypeDAL.default_kwargs | kw
        return Field(name, field_type, **kw_combined)
