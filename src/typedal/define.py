from __future__ import annotations

import copy
import types
import typing
import warnings
from typing import Any, Optional, Type

import pydal

from .constants import BASIC_MAPPINGS
from .core import TypeDAL, evaluate_forward_reference
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


class TableDefinitionBuilder:
    """Handles the conversion of TypedTable classes to pydal tables."""

    def __init__(self, dal: "TypeDAL"):
        self.dal = dal
        self.class_map: dict[str, Type["TypedTable"]] = {}

    def define(self, cls: Type[T], **kwargs: Any) -> Type[T]:
        """Build and register a table from a TypedTable class."""
        full_dict = all_dict(cls)
        tablename = to_snake(cls.__name__)
        annotations = all_annotations(cls)
        annotations |= {k: typing.cast(type, v) for k, v in full_dict.items() if is_typed_field(v)}
        annotations = {k: v for k, v in annotations.items() if not k.startswith("_")}

        typedfields: dict[str, TypedField[Any]] = {
            k: instanciate(v, True) for k, v in annotations.items() if is_typed_field(v)
        }

        relationships: dict[str, type[Relationship[Any]]] = filter_out(annotations, Relationship)
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

        cache_dependency = self.dal._config.caching and kwargs.pop("cache_dependency", True)
        table: Table = self.dal.define_table(tablename, *fields.values(), **kwargs)

        for name, typed_field in typedfields.items():
            field = fields[name]
            typed_field.bind(field, table)

        if issubclass(cls, TypedTable):
            cls.__set_internals__(
                db=self.dal,
                table=table,
                relationships=typing.cast(dict[str, Relationship[Any]], relationships),
            )
            self.class_map[str(table)] = cls
            self.class_map[table._rname] = cls
            cls.__on_define__(self.dal)
        else:
            warnings.warn("db.define used without inheriting TypedTable. This could lead to strange problems!")

        if not tablename.startswith("typedal_") and cache_dependency:
            from .caching import _remove_cache

            table._before_update.append(lambda s, _: _remove_cache(s, tablename))
            table._before_delete.append(lambda s: _remove_cache(s, tablename))

        return cls

    @classmethod
    def to_field(cls, fname: str, ftype: type, **kw: Any) -> Field:
        """Convert annotation to pydal Field."""
        fname = to_snake(fname)
        if converted_type := cls.annotation_to_pydal_fieldtype(ftype, kw):
            return cls.build_field(fname, converted_type, **kw)
        else:
            raise NotImplementedError(f"Unsupported type {ftype}/{type(ftype)}")

    @classmethod
    def annotation_to_pydal_fieldtype(
        cls,
        ftype_annotation: T_annotation,
        mut_kw: typing.MutableMapping[str, Any],
    ) -> Optional[str]:
        """Convert Python type annotation to pydal field type string."""
        ftype = typing.cast(type, ftype_annotation)

        if isinstance(ftype, str):
            fw_ref: typing.ForwardRef = typing.get_args(Type[ftype])[0]
            ftype = evaluate_forward_reference(fw_ref)

        if mapping := BASIC_MAPPINGS.get(ftype):
            return mapping
        elif isinstance(ftype, pydal.objects.Table):
            return f"reference {ftype._tablename}"
        elif issubclass(type(ftype), type) and issubclass(ftype, TypedTable):
            snakename = to_snake(ftype.__name__)
            return f"reference {snakename}"
        elif isinstance(ftype, TypedField):
            return ftype._to_field(mut_kw)
        elif origin_is_subclass(ftype, TypedField):
            return cls.annotation_to_pydal_fieldtype(typing.get_args(ftype)[0], mut_kw)
        elif isinstance(ftype, types.GenericAlias) and typing.get_origin(ftype) in (list, TypedField):
            child_type = typing.get_args(ftype)[0]
            child_type = cls.annotation_to_pydal_fieldtype(child_type, mut_kw)
            return f"list:{child_type}"
        elif is_union(ftype):
            match typing.get_args(ftype):
                case (child_type, _Types.NONETYPE) | (_Types.NONETYPE, child_type):
                    mut_kw["notnull"] = False
                    return cls.annotation_to_pydal_fieldtype(child_type, mut_kw)
                case _:
                    return None
        else:
            return None

    @classmethod
    def build_field(cls, name: str, field_type: str, **kw: Any) -> Field:
        """Create a pydal Field with default kwargs."""
        kw_combined = TypeDAL.default_kwargs | kw
        return Field(name, field_type, **kw_combined)
