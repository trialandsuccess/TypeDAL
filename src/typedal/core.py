"""
Core functionality of TypeDAL.
"""

import contextlib
import csv
import datetime as dt
import functools
import inspect
import json
import math
import sys
import types
import typing
import uuid
import warnings
from collections import defaultdict
from copy import copy
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional, Type

import pydal
from pydal._globals import DEFAULT
from pydal.objects import Field as _Field
from pydal.objects import Query as _Query
from pydal.objects import Row
from pydal.objects import Table as _Table
from typing_extensions import Self, Unpack

from .config import TypeDALConfig, load_config
from .helpers import (
    DummyQuery,
    all_annotations,
    all_dict,
    as_lambda,
    classproperty,
    extract_type_optional,
    filter_out,
    instanciate,
    is_union,
    looks_like,
    mktable,
    origin_is_subclass,
    to_snake,
    unwrap_type,
)
from .serializers import as_json
from .types import (
    AnyDict,
    CacheMetadata,
    Expression,
    Field,
    FieldSettings,
    Metadata,
    OpRow,
    PaginateDict,
    Pagination,
    Query,
    Reference,
    Rows,
    SelectKwargs,
    Set,
    Table,
    Validator,
    _Types,
)

# use typing.cast(type, ...) to make mypy happy with unions
T_annotation = Type[Any] | types.UnionType
T_Query = typing.Union["Table", Query, bool, None, "TypedTable", Type["TypedTable"]]
T_Value = typing.TypeVar("T_Value")  # actual type of the Field (via Generic)
T_MetaInstance = typing.TypeVar("T_MetaInstance", bound="TypedTable")  # bound="TypedTable"; bound="TableMeta"
T = typing.TypeVar("T")

BASIC_MAPPINGS: dict[T_annotation, str] = {
    str: "string",
    int: "integer",
    bool: "boolean",
    bytes: "blob",
    float: "double",
    object: "json",
    Decimal: "decimal(10,2)",
    dt.date: "date",
    dt.time: "time",
    dt.datetime: "datetime",
}


def is_typed_field(cls: Any) -> typing.TypeGuard["TypedField[Any]"]:
    """
    Is `cls` an instance or subclass of TypedField?

    Deprecated
    """
    return isinstance(cls, TypedField) or (
        isinstance(typing.get_origin(cls), type) and issubclass(typing.get_origin(cls), TypedField)
    )


JOIN_OPTIONS = typing.Literal["left", "inner", None]
DEFAULT_JOIN_OPTION: JOIN_OPTIONS = "left"

# table-ish paramter:
P_Table = typing.Union[Type["TypedTable"], pydal.objects.Table]

Condition: typing.TypeAlias = typing.Optional[
    typing.Callable[
        # self, other -> Query
        [P_Table, P_Table],
        Query | bool,
    ]
]

OnQuery: typing.TypeAlias = typing.Optional[
    typing.Callable[
        # self, other -> list of .on statements
        [P_Table, P_Table],
        list[Expression],
    ]
]

To_Type = typing.TypeVar("To_Type", type[Any], Type[Any], str)


class Relationship(typing.Generic[To_Type]):
    """
    Define a relationship to another table.
    """

    _type: To_Type
    table: Type["TypedTable"] | type | str
    condition: Condition
    condition_and: Condition
    on: OnQuery
    multiple: bool
    join: JOIN_OPTIONS

    def __init__(
        self,
        _type: To_Type,
        condition: Condition = None,
        join: JOIN_OPTIONS = None,
        on: OnQuery = None,
        condition_and: Condition = None,
    ):
        """
        Should not be called directly, use relationship() instead!
        """
        if condition and on:
            warnings.warn(f"Relation | Both specified! {condition=} {on=} {_type=}")
            raise ValueError("Please specify either a condition or an 'on' statement for this relationship!")

        self._type = _type
        self.condition = condition
        self.join = "left" if on else join  # .on is always left join!
        self.on = on
        self.condition_and = condition_and

        if args := typing.get_args(_type):
            self.table = unwrap_type(args[0])
            self.multiple = True
        else:
            self.table = _type
            self.multiple = False

        if isinstance(self.table, str):
            self.table = TypeDAL.to_snake(self.table)

    def clone(self, **update: Any) -> "Relationship[To_Type]":
        """
        Create a copy of the relationship, possibly updated.
        """
        return self.__class__(
            update.get("_type") or self._type,
            update.get("condition") or self.condition,
            update.get("join") or self.join,
            update.get("on") or self.on,
            update.get("condition_and") or self.condition_and,
        )

    def __repr__(self) -> str:
        """
        Representation of the relationship.
        """
        if callback := self.condition or self.on:
            src_code = inspect.getsource(callback).strip()

            if c_and := self.condition_and:
                and_code = inspect.getsource(c_and).strip()
                src_code += " AND " + and_code
        else:
            cls_name = self._type if isinstance(self._type, str) else self._type.__name__
            src_code = f"to {cls_name} (missing condition)"

        join = f":{self.join}" if self.join else ""
        return f"<Relationship{join} {src_code}>"

    def get_table(self, db: "TypeDAL") -> Type["TypedTable"]:
        """
        Get the table this relationship is bound to.
        """
        table = self.table  # can be a string because db wasn't available yet
        if isinstance(table, str):
            if mapped := db._class_map.get(table):
                # yay
                return mapped

            # boo, fall back to untyped table but pretend it is typed:
            return typing.cast(Type["TypedTable"], db[table])  # eh close enough!

        return table

    def get_table_name(self) -> str:
        """
        Get the name of the table this relationship is bound to.
        """
        if isinstance(self.table, str):
            return self.table

        if isinstance(self.table, pydal.objects.Table):
            return str(self.table)

        # else: typed table
        try:
            table = self.table._ensure_table_defined() if issubclass(self.table, TypedTable) else self.table
        except Exception:  # pragma: no cover
            table = self.table

        return str(table)

    def __get__(self, instance: Any, owner: Any) -> typing.Optional[list[Any]] | "Relationship[To_Type]":
        """
        Relationship is a descriptor class, which can be returned from a class but not an instance.

        For an instance, using .join() will replace the Relationship with the actual data.
        If you forgot to join, a warning will be shown and empty data will be returned.
        """
        if not instance:
            # relationship queried on class, that's allowed
            return self

        warnings.warn(
            "Trying to get data from a relationship object! Did you forget to join it?", category=RuntimeWarning
        )
        if self.multiple:
            return []
        else:
            return None


def relationship(_type: To_Type, condition: Condition = None, join: JOIN_OPTIONS = None, on: OnQuery = None) -> To_Type:
    """
    Define a relationship to another table, when its id is not stored in the current table.

    Example:
        class User(TypedTable):
            name: str

            posts = relationship(list["Post"], condition=lambda self, post: self.id == post.author, join='left')

        class Post(TypedTable):
            title: str
            author: User

    User.join("posts").first() # User instance with list[Post] in .posts

    Here, Post stores the User ID, but `relationship(list["Post"])` still allows you to get the user's posts.
    In this case, the join strategy is set to LEFT so users without posts are also still selected.

    For complex queries with a pivot table, a `on` can be set insteaad of `condition`:
        class User(TypedTable):
        ...

        tags = relationship(list["Tag"], on=lambda self, tag: [
                Tagged.on(Tagged.entity == entity.gid),
                Tag.on((Tagged.tag == tag.id)),
            ])

    If you'd try to capture this in a single 'condition', pydal would create a cross join which is much less efficient.
    """
    return typing.cast(
        # note: The descriptor `Relationship[To_Type]` is more correct, but pycharm doesn't really get that.
        # so for ease of use, just cast to the refered type for now!
        # e.g. x = relationship(Author) -> x: Author
        To_Type,
        Relationship(_type, condition, join, on),
    )


T_Field: typing.TypeAlias = typing.Union["TypedField[Any]", "Table", Type["TypedTable"]]


def _generate_relationship_condition(_: Type["TypedTable"], key: str, field: T_Field) -> Condition:
    origin = typing.get_origin(field)
    # else: generic

    if origin is list:
        # field = typing.get_args(field)[0]  # actual field
        # return lambda _self, _other: cls[key].contains(field)

        return lambda _self, _other: _self[key].contains(_other.id)
    else:
        # normal reference
        # return lambda _self, _other: cls[key] == field.id
        return lambda _self, _other: _self[key] == _other.id


def to_relationship(
    cls: Type["TypedTable"] | type[Any],
    key: str,
    field: T_Field,
) -> typing.Optional[Relationship[Any]]:
    """
    Used to automatically create relationship instance for reference fields.

    Example:
        class MyTable(TypedTable):
            reference: OtherTable

    `reference` contains the id of an Other Table row.
     MyTable.relationships should have 'reference' as a relationship, so `MyTable.join('reference')` should work.

     This function will automatically perform this logic (called in db.define):
        to_relationship(MyTable, 'reference', OtherTable) -> Relationship[OtherTable]

    Also works for list:reference (list[OtherTable]) and TypedField[OtherTable].
    """
    if looks_like(field, TypedField):
        # typing.get_args works for list[str] but not for TypedField[role] :(
        if args := typing.get_args(field):
            # TypedField[SomeType] -> SomeType
            field = args[0]
        elif hasattr(field, "_type"):
            # TypedField(SomeType) -> SomeType
            field = typing.cast(T_Field, field._type)
        else:  # pragma: no cover
            # weird
            return None

    field, optional = extract_type_optional(field)

    try:
        condition = _generate_relationship_condition(cls, key, field)
    except Exception as e:  # pragma: no cover
        warnings.warn("Could not generate Relationship condition", source=e)
        condition = None

    if not condition:  # pragma: no cover
        # something went wrong, not a valid relationship
        warnings.warn(f"Invalid relationship for {cls.__name__}.{key}: {field}")
        return None

    join = "left" if optional or typing.get_origin(field) is list else "inner"

    return Relationship(typing.cast(type[TypedTable], field), condition, typing.cast(JOIN_OPTIONS, join))


def evaluate_forward_reference(fw_ref: typing.ForwardRef) -> type:
    """
    Extract the original type from a forward reference string.
    """
    kwargs = dict(
        localns=locals(),
        globalns=globals(),
        recursive_guard=frozenset(),
    )
    if sys.version_info >= (3, 13):  # pragma: no cover
        # suggested since 3.13 (warning) and not supported before. Mandatory after 1.15!
        kwargs["type_params"] = ()

    return fw_ref._evaluate(**kwargs)  # type: ignore


class TypeDAL(pydal.DAL):  # type: ignore
    """
    Drop-in replacement for pyDAL with layer to convert class-based table definitions to classical pydal define_tables.
    """

    _config: TypeDALConfig

    def __init__(
        self,
        uri: Optional[str] = None,  # default from config or 'sqlite:memory'
        pool_size: int = None,  # default 1 if sqlite else 3
        folder: Optional[str | Path] = None,  # default 'databases' in config
        db_codec: str = "UTF-8",
        check_reserved: Optional[list[str]] = None,
        migrate: Optional[bool] = None,  # default True by config
        fake_migrate: Optional[bool] = None,  # default False by config
        migrate_enabled: bool = True,
        fake_migrate_all: bool = False,
        decode_credentials: bool = False,
        driver_args: Optional[AnyDict] = None,
        adapter_args: Optional[AnyDict] = None,
        attempts: int = 5,
        auto_import: bool = False,
        bigint_id: bool = False,
        debug: bool = False,
        lazy_tables: bool = False,
        db_uid: Optional[str] = None,
        after_connection: typing.Callable[..., Any] = None,
        tables: Optional[list[str]] = None,
        ignore_field_case: bool = True,
        entity_quoting: bool = True,
        table_hash: Optional[str] = None,
        enable_typedal_caching: bool = None,
        use_pyproject: bool | str = True,
        use_env: bool | str = True,
        connection: Optional[str] = None,
        config: Optional[TypeDALConfig] = None,
    ) -> None:
        """
        Adds some internal tables after calling pydal's default init.

        Set enable_typedal_caching to False to disable this behavior.
        """
        config = config or load_config(connection, _use_pyproject=use_pyproject, _use_env=use_env)
        config.update(
            database=uri,
            dialect=uri.split(":")[0] if uri and ":" in uri else None,
            folder=str(folder) if folder is not None else None,
            migrate=migrate,
            fake_migrate=fake_migrate,
            caching=enable_typedal_caching,
            pool_size=pool_size,
        )

        self._config = config
        self.db = self

        if config.folder:
            Path(config.folder).mkdir(exist_ok=True)

        super().__init__(
            config.database,
            config.pool_size,
            config.folder,
            db_codec,
            check_reserved,
            config.migrate,
            config.fake_migrate,
            migrate_enabled,
            fake_migrate_all,
            decode_credentials,
            driver_args,
            adapter_args,
            attempts,
            auto_import,
            bigint_id,
            debug,
            lazy_tables,
            db_uid,
            after_connection,
            tables,
            ignore_field_case,
            entity_quoting,
            table_hash,
        )

        if config.caching:
            self.try_define(_TypedalCache)
            self.try_define(_TypedalCacheDependency)

    def try_define(self, model: Type[T], verbose: bool = False) -> Type[T]:
        """
        Try to define a model with migrate or fall back to fake migrate.
        """
        try:
            return self.define(model, migrate=True)
        except Exception as e:
            # clean up:
            self.rollback()
            if (tablename := self.to_snake(model.__name__)) and tablename in dir(self):
                delattr(self, tablename)

            if verbose:
                warnings.warn(f"{model} could not be migrated, try faking", source=e, category=RuntimeWarning)

            # try again:
            return self.define(model, migrate=True, fake_migrate=True, redefine=True)

    default_kwargs: typing.ClassVar[AnyDict] = {
        # fields are 'required' (notnull) by default:
        "notnull": True,
    }

    # maps table name to typedal class, for resolving future references
    _class_map: typing.ClassVar[dict[str, Type["TypedTable"]]] = {}

    def _define(self, cls: Type[T], **kwargs: Any) -> Type[T]:
        # todo: new relationship item added should also invalidate (previously unrelated) cache result

        # todo: option to enable/disable cache dependency behavior:
        #   - don't set _before_update and _before_delete
        #   - don't add TypedalCacheDependency entry
        #   - don't invalidate other item on new row of this type

        # when __future__.annotations is implemented, cls.__annotations__ will not work anymore as below.
        # proper way to handle this would be (but gives error right now due to Table implementing magic methods):
        # typing.get_type_hints(cls, globalns=None, localns=None)
        # -> ERR e.g. `pytest -svxk cli` -> name 'BestFriend' is not defined

        # dirty way (with evil eval):
        # [eval(v) for k, v in cls.__annotations__.items()]
        # this however also stops working when variables outside this scope or even references to other
        # objects are used. So for now, this package will NOT work when from __future__ import annotations is used,
        # and might break in the future, when this annotations behavior is enabled by default.

        # non-annotated variables have to be passed to define_table as kwargs
        full_dict = all_dict(cls)  # includes properties from parents (e.g. useful for mixins)

        tablename = self.to_snake(cls.__name__)
        # grab annotations of cls and it's parents:
        annotations = all_annotations(cls)
        # extend with `prop = TypedField()` 'annotations':
        annotations |= {k: typing.cast(type, v) for k, v in full_dict.items() if is_typed_field(v)}
        # remove internal stuff:
        annotations = {k: v for k, v in annotations.items() if not k.startswith("_")}

        typedfields: dict[str, TypedField[Any]] = {
            k: instanciate(v, True) for k, v in annotations.items() if is_typed_field(v)
        }

        relationships: dict[str, type[Relationship[Any]]] = filter_out(annotations, Relationship)

        fields = {fname: self._to_field(fname, ftype) for fname, ftype in annotations.items()}

        # ! dont' use full_dict here:
        other_kwargs = kwargs | {
            k: v for k, v in cls.__dict__.items() if k not in annotations and not k.startswith("_")
        }  # other_kwargs was previously used to pass kwargs to typedal, but use @define(**kwargs) for that.
        #    now it's only used to extract relationships from the object.
        #    other properties of the class (incl methods) should not be touched

        # for key in typedfields.keys() - full_dict.keys():
        #     # typed fields that don't haven't been added to the object yet
        #     setattr(cls, key, typedfields[key])

        for key, field in typedfields.items():
            # clone every property so it can be re-used across mixins:
            clone = copy(field)
            setattr(cls, key, clone)
            typedfields[key] = clone

        # start with base classes and overwrite with current class:
        relationships = filter_out(full_dict, Relationship) | relationships | filter_out(other_kwargs, Relationship)

        # DEPRECATED: Relationship as annotation is currently not supported!
        # ensure they are all instances and
        # not mix of instances (`= relationship()`) and classes (`: Relationship[...]`):
        # relationships = {
        #     k: v if isinstance(v, Relationship) else to_relationship(cls, k, v) for k, v in relationships.items()
        # }

        # keys of implicit references (also relationships):
        reference_field_keys = [
            k for k, v in fields.items() if str(v.type).split(" ")[0] in ("list:reference", "reference")
        ]

        # add implicit relationships:
        # User; list[User]; TypedField[User]; TypedField[list[User]]; TypedField(User); TypedField(list[User])
        relationships |= {
            k: new_relationship
            for k in reference_field_keys
            if k not in relationships and (new_relationship := to_relationship(cls, k, annotations[k]))
        }

        # fixme: list[Reference] is recognized as relationship,
        #        TypedField(list[Reference]) is NOT recognized!!!

        cache_dependency = self._config.caching and kwargs.pop("cache_dependency", True)

        table: Table = self.define_table(tablename, *fields.values(), **kwargs)

        for name, typed_field in typedfields.items():
            field = fields[name]
            typed_field.bind(field, table)

        if issubclass(cls, TypedTable):
            cls.__set_internals__(
                db=self,
                table=table,
                # by now, all relationships should be instances!
                relationships=typing.cast(dict[str, Relationship[Any]], relationships),
            )
            self._class_map[str(table)] = cls
            cls.__on_define__(self)
        else:
            warnings.warn("db.define used without inheriting TypedTable. This could lead to strange problems!")

        if not tablename.startswith("typedal_") and cache_dependency:
            table._before_update.append(lambda s, _: _remove_cache(s, tablename))
            table._before_delete.append(lambda s: _remove_cache(s, tablename))

        return cls

    @typing.overload
    def define(self, maybe_cls: None = None, **kwargs: Any) -> typing.Callable[[Type[T]], Type[T]]:
        """
        Typing Overload for define without a class.

        @db.define()
        class MyTable(TypedTable): ...
        """

    @typing.overload
    def define(self, maybe_cls: Type[T], **kwargs: Any) -> Type[T]:
        """
        Typing Overload for define with a class.

        @db.define
        class MyTable(TypedTable): ...
        """

    def define(self, maybe_cls: Type[T] | None = None, **kwargs: Any) -> Type[T] | typing.Callable[[Type[T]], Type[T]]:
        """
        Can be used as a decorator on a class that inherits `TypedTable`, \
          or as a regular method if you need to define your classes before you have access to a 'db' instance.

        You can also pass extra arguments to db.define_table.
            See http://www.web2py.com/books/default/chapter/29/06/the-database-abstraction-layer#Table-constructor

        Example:
            @db.define
            class Person(TypedTable):
                ...

            class Article(TypedTable):
                ...

            # at a later time:
            db.define(Article)

        Returns:
            the result of pydal.define_table
        """

        def wrapper(cls: Type[T]) -> Type[T]:
            return self._define(cls, **kwargs)

        if maybe_cls:
            return wrapper(maybe_cls)

        return wrapper

    # def drop(self, table_name: str) -> None:
    #     """
    #     Remove a table by name (both on the database level and the typedal level).
    #     """
    #     # drop calls TypedTable.drop() and removes it from the `_class_map`
    #     if cls := self._class_map.pop(table_name, None):
    #         cls.drop()

    # def drop_all(self, max_retries: int = None) -> None:
    #     """
    #     Remove all tables and keep doing so until everything is gone!
    #     """
    #     retries = 0
    #     if max_retries is None:
    #         max_retries = len(self.tables)
    #
    #     while self.tables:
    #         retries += 1
    #         for table in self.tables:
    #             self.drop(table)
    #
    #         if retries > max_retries:
    #             raise RuntimeError("Could not delete all tables")

    def __call__(self, *_args: T_Query, **kwargs: Any) -> "TypedSet":
        """
        A db instance can be called directly to perform a query.

        Usually, only a query is passed.

        Example:
            db(query).select()

        """
        args = list(_args)
        if args:
            cls = args[0]
            if isinstance(cls, bool):
                raise ValueError("Don't actually pass a bool to db()! Use a query instead.")

            if isinstance(cls, type) and issubclass(type(cls), type) and issubclass(cls, TypedTable):
                # table defined without @db.define decorator!
                _cls: Type[TypedTable] = cls
                args[0] = _cls.id != None

        _set = super().__call__(*args, **kwargs)
        return typing.cast(TypedSet, _set)

    def __getitem__(self, key: str) -> "Table":
        """
        Allows dynamically accessing a table by its name as a string.

        If you need the TypedTable class instead of the pydal table, use find_model instead.

        Example:
            db['users'] -> user
        """
        return typing.cast(Table, super().__getitem__(str(key)))

    def find_model(self, table_name: str) -> Type["TypedTable"] | None:
        """
        Retrieves a mapped table class by its name.

        This method searches for a table class matching the given table name
        in the defined class map dictionary. If a match is found, the corresponding
        table class is returned; otherwise, None is returned, indicating that no
        table class matches the input name.

        Args:
            table_name: The name of the table to retrieve the mapped class for.

        Returns:
            The mapped table class if it exists, otherwise None.
        """
        return self._class_map.get(table_name, None)

    @classmethod
    def _build_field(cls, name: str, _type: str, **kw: Any) -> Field:
        # return Field(name, _type, **{**cls.default_kwargs, **kw})
        kw_combined = cls.default_kwargs | kw
        return Field(name, _type, **kw_combined)

    @classmethod
    def _annotation_to_pydal_fieldtype(
        cls, _ftype: T_annotation, mut_kw: typing.MutableMapping[str, Any]
    ) -> Optional[str]:
        # ftype can be a union or type. typing.cast is sometimes used to tell mypy when it's not a union.
        ftype = typing.cast(type, _ftype)  # cast from Type to type to make mypy happy)

        if isinstance(ftype, str):
            # extract type from string
            fw_ref: typing.ForwardRef = typing.get_args(Type[ftype])[0]
            ftype = evaluate_forward_reference(fw_ref)

        if mapping := BASIC_MAPPINGS.get(ftype):
            # basi types
            return mapping
        elif isinstance(ftype, _Table):
            # db.table
            return f"reference {ftype._tablename}"
        elif issubclass(type(ftype), type) and issubclass(ftype, TypedTable):
            # SomeTable
            snakename = cls.to_snake(ftype.__name__)
            return f"reference {snakename}"
        elif isinstance(ftype, TypedField):
            # FieldType(type, ...)
            return ftype._to_field(mut_kw)
        elif origin_is_subclass(ftype, TypedField):
            # TypedField[int]
            return cls._annotation_to_pydal_fieldtype(typing.get_args(ftype)[0], mut_kw)
        elif isinstance(ftype, types.GenericAlias) and typing.get_origin(ftype) in (list, TypedField):
            # list[str] -> str -> string -> list:string
            _child_type = typing.get_args(ftype)[0]
            _child_type = cls._annotation_to_pydal_fieldtype(_child_type, mut_kw)
            return f"list:{_child_type}"
        elif is_union(ftype):
            # str | int -> UnionType
            # typing.Union[str | int] -> typing._UnionGenericAlias

            # Optional[type] == type | None

            match typing.get_args(ftype):
                case (_child_type, _Types.NONETYPE) | (_Types.NONETYPE, _child_type):
                    # good union of Nullable

                    # if a field is optional, it is nullable:
                    mut_kw["notnull"] = False
                    return cls._annotation_to_pydal_fieldtype(_child_type, mut_kw)
                case _:
                    # two types is not supported by the db!
                    return None
        else:
            return None

    @classmethod
    def _to_field(cls, fname: str, ftype: type, **kw: Any) -> Field:
        """
        Convert a annotation into a pydal Field.

        Args:
            fname: name of the property
            ftype: annotation of the property
            kw: when using TypedField or a function returning it (e.g. StringField),
                keyword args can be used to pass any other settings you would normally to a pydal Field

        -> pydal.Field(fname, ftype, **kw)

        Example:
            class MyTable:
                fname: ftype
                id: int
                name: str
                reference: Table
                other: TypedField(str, default="John Doe")  # default will be in kwargs
        """
        fname = cls.to_snake(fname)

        # note: 'kw' is updated in `_annotation_to_pydal_fieldtype` by the kwargs provided to the TypedField(...)
        if converted_type := cls._annotation_to_pydal_fieldtype(ftype, kw):
            return cls._build_field(fname, converted_type, **kw)
        else:
            raise NotImplementedError(f"Unsupported type {ftype}/{type(ftype)}")

    @staticmethod
    def to_snake(camel: str) -> str:
        """
        Moved to helpers, kept as a static method for legacy reasons.
        """
        return to_snake(camel)


P = typing.ParamSpec("P")
R = typing.TypeVar("R")


class TableMeta(type):
    """
    This metaclass contains functionality on table classes, that doesn't exist on its instances.

    Example:
        class MyTable(TypedTable):
            some_field: TypedField[int]

    MyTable.update_or_insert(...) # should work

    MyTable.some_field # -> Field, can be used to query etc.

    row = MyTable.first() # returns instance of MyTable

    # row.update_or_insert(...) # shouldn't work!

    row.some_field # -> int, with actual data

    """

    # set up by db.define:
    # _db: TypeDAL | None = None
    # _table: Table | None = None
    _db: TypeDAL | None = None
    _table: Table | None = None
    _relationships: dict[str, Relationship[Any]] | None = None

    #########################
    # TypeDAL custom logic: #
    #########################

    def __set_internals__(self, db: pydal.DAL, table: Table, relationships: dict[str, Relationship[Any]]) -> None:
        """
        Store the related database and pydal table for later usage.
        """
        self._db = db
        self._table = table
        self._relationships = relationships

    def __getattr__(self, col: str) -> Optional[Field]:
        """
        Magic method used by TypedTableMeta to get a database field with dot notation on a class.

        Example:
            SomeTypedTable.col -> db.table.col (via TypedTableMeta.__getattr__)

        """
        if self._table:
            return getattr(self._table, col, None)

        return None

    def _ensure_table_defined(self) -> Table:
        if not self._table:
            raise EnvironmentError("@define or db.define is not called on this class yet!")
        return self._table

    def __iter__(self) -> typing.Generator[Field, None, None]:
        """
        Loop through the columns of this model.
        """
        table = self._ensure_table_defined()
        yield from iter(table)

    def __getitem__(self, item: str) -> Field:
        """
        Allow dict notation to get a column of this table (-> Field instance).
        """
        table = self._ensure_table_defined()
        return table[item]

    def __str__(self) -> str:
        """
        Normally, just returns the underlying table name, but with a fallback if the model is unbound.
        """
        if self._table:
            return str(self._table)
        else:
            return f"<unbound table {self.__name__}>"

    def from_row(self: Type[T_MetaInstance], row: pydal.objects.Row) -> T_MetaInstance:
        """
        Create a model instance from a pydal row.
        """
        return self(row)

    def all(self: Type[T_MetaInstance]) -> "TypedRows[T_MetaInstance]":
        """
        Return all rows for this model.
        """
        return self.collect()

    def get_relationships(self) -> dict[str, Relationship[Any]]:
        """
        Return the registered relationships of the current model.
        """
        return self._relationships or {}

    ##########################
    # TypeDAL Modified Logic #
    ##########################

    def insert(self: Type[T_MetaInstance], **fields: Any) -> T_MetaInstance:
        """
        This is only called when db.define is not used as a decorator.

        cls.__table functions as 'self'

        Args:
            **fields: anything you want to insert in the database

        Returns: the ID of the new row.

        """
        table = self._ensure_table_defined()

        result = table.insert(**fields)
        # it already is an int but mypy doesn't understand that
        return self(result)

    def _insert(self, **fields: Any) -> str:
        table = self._ensure_table_defined()

        return str(table._insert(**fields))

    def bulk_insert(self: Type[T_MetaInstance], items: list[AnyDict]) -> "TypedRows[T_MetaInstance]":
        """
        Insert multiple rows, returns a TypedRows set of new instances.
        """
        table = self._ensure_table_defined()
        result = table.bulk_insert(items)
        return self.where(lambda row: row.id.belongs(result)).collect()

    def update_or_insert(
        self: Type[T_MetaInstance], query: T_Query | AnyDict = DEFAULT, **values: Any
    ) -> T_MetaInstance:
        """
        Update a row if query matches, else insert a new one.

        Returns the created or updated instance.
        """
        table = self._ensure_table_defined()

        if query is DEFAULT:
            record = table(**values)
        elif isinstance(query, dict):
            record = table(**query)
        else:
            record = table(query)

        if not record:
            return self.insert(**values)

        record.update_record(**values)
        return self(record)

    def validate_and_insert(
        self: Type[T_MetaInstance], **fields: Any
    ) -> tuple[Optional[T_MetaInstance], Optional[dict[str, str]]]:
        """
        Validate input data and then insert a row.

        Returns a tuple of (the created instance, a dict of errors).
        """
        table = self._ensure_table_defined()
        result = table.validate_and_insert(**fields)
        if row_id := result.get("id"):
            return self(row_id), None
        else:
            return None, result.get("errors")

    def validate_and_update(
        self: Type[T_MetaInstance], query: Query, **fields: Any
    ) -> tuple[Optional[T_MetaInstance], Optional[dict[str, str]]]:
        """
        Validate input data and then update max 1 row.

        Returns a tuple of (the updated instance, a dict of errors).
        """
        table = self._ensure_table_defined()

        result = table.validate_and_update(query, **fields)

        if errors := result.get("errors"):
            return None, errors
        elif row_id := result.get("id"):
            return self(row_id), None
        else:  # pragma: no cover
            # update on query without result (shouldnt happen)
            return None, None

    def validate_and_update_or_insert(
        self: Type[T_MetaInstance], query: Query, **fields: Any
    ) -> tuple[Optional[T_MetaInstance], Optional[dict[str, str]]]:
        """
        Validate input data and then update_and_insert (on max 1 row).

        Returns a tuple of (the updated/created instance, a dict of errors).
        """
        table = self._ensure_table_defined()
        result = table.validate_and_update_or_insert(query, **fields)

        if errors := result.get("errors"):
            return None, errors
        elif row_id := result.get("id"):
            return self(row_id), None
        else:  # pragma: no cover
            # update on query without result (shouldnt happen)
            return None, None

    def select(self: Type[T_MetaInstance], *a: Any, **kw: Any) -> "QueryBuilder[T_MetaInstance]":
        """
        See QueryBuilder.select!
        """
        return QueryBuilder(self).select(*a, **kw)

    def column(self: Type[T_MetaInstance], field: "TypedField[T] | T", **options: Unpack[SelectKwargs]) -> list[T]:
        """
        Get all values in a specific column.

        Shortcut for `.select(field).execute().column(field)`.
        """
        return QueryBuilder(self).select(field, **options).execute().column(field)

    def paginate(self: Type[T_MetaInstance], limit: int, page: int = 1) -> "PaginatedRows[T_MetaInstance]":
        """
        See QueryBuilder.paginate!
        """
        return QueryBuilder(self).paginate(limit=limit, page=page)

    def chunk(self: Type[T_MetaInstance], chunk_size: int) -> typing.Generator["TypedRows[T_MetaInstance]", Any, None]:
        """
        See QueryBuilder.chunk!
        """
        return QueryBuilder(self).chunk(chunk_size)

    def where(self: Type[T_MetaInstance], *a: Any, **kw: Any) -> "QueryBuilder[T_MetaInstance]":
        """
        See QueryBuilder.where!
        """
        return QueryBuilder(self).where(*a, **kw)

    def cache(self: Type[T_MetaInstance], *deps: Any, **kwargs: Any) -> "QueryBuilder[T_MetaInstance]":
        """
        See QueryBuilder.cache!
        """
        return QueryBuilder(self).cache(*deps, **kwargs)

    def count(self: Type[T_MetaInstance]) -> int:
        """
        See QueryBuilder.count!
        """
        return QueryBuilder(self).count()

    def exists(self: Type[T_MetaInstance]) -> bool:
        """
        See QueryBuilder.exists!
        """
        return QueryBuilder(self).exists()

    def first(self: Type[T_MetaInstance]) -> T_MetaInstance | None:
        """
        See QueryBuilder.first!
        """
        return QueryBuilder(self).first()

    def first_or_fail(self: Type[T_MetaInstance]) -> T_MetaInstance:
        """
        See QueryBuilder.first_or_fail!
        """
        return QueryBuilder(self).first_or_fail()

    def join(
        self: Type[T_MetaInstance],
        *fields: str | Type["TypedTable"],
        method: JOIN_OPTIONS = None,
        on: OnQuery | list[Expression] | Expression = None,
        condition: Condition = None,
        condition_and: Condition = None,
    ) -> "QueryBuilder[T_MetaInstance]":
        """
        See QueryBuilder.join!
        """
        return QueryBuilder(self).join(*fields, on=on, condition=condition, method=method, condition_and=condition_and)

    def collect(self: Type[T_MetaInstance], verbose: bool = False) -> "TypedRows[T_MetaInstance]":
        """
        See QueryBuilder.collect!
        """
        return QueryBuilder(self).collect(verbose=verbose)

    @property
    def ALL(cls) -> pydal.objects.SQLALL:
        """
        Select all fields for this table.
        """
        table = cls._ensure_table_defined()

        return table.ALL

    ##########################
    # TypeDAL Shadowed Logic #
    ##########################
    fields: list[str]

    # other table methods:

    def truncate(self, mode: str = "") -> None:
        """
        Remove all data and reset index.
        """
        table = self._ensure_table_defined()
        table.truncate(mode)

    def drop(self, mode: str = "") -> None:
        """
        Remove the underlying table.
        """
        table = self._ensure_table_defined()
        table.drop(mode)

    def create_index(self, name: str, *fields: Field | str, **kwargs: Any) -> bool:
        """
        Add an index on some columns of this table.
        """
        table = self._ensure_table_defined()
        result = table.create_index(name, *fields, **kwargs)
        return typing.cast(bool, result)

    def drop_index(self, name: str, if_exists: bool = False) -> bool:
        """
        Remove an index from this table.
        """
        table = self._ensure_table_defined()
        result = table.drop_index(name, if_exists)
        return typing.cast(bool, result)

    def import_from_csv_file(
        self,
        csvfile: typing.TextIO,
        id_map: dict[str, str] = None,
        null: Any = "<NULL>",
        unique: str = "uuid",
        id_offset: dict[str, int] = None,  # id_offset used only when id_map is None
        transform: typing.Callable[[dict[Any, Any]], dict[Any, Any]] = None,
        validate: bool = False,
        encoding: str = "utf-8",
        delimiter: str = ",",
        quotechar: str = '"',
        quoting: int = csv.QUOTE_MINIMAL,
        restore: bool = False,
        **kwargs: Any,
    ) -> None:
        """
        Load a csv file into the database.
        """
        table = self._ensure_table_defined()
        table.import_from_csv_file(
            csvfile,
            id_map=id_map,
            null=null,
            unique=unique,
            id_offset=id_offset,
            transform=transform,
            validate=validate,
            encoding=encoding,
            delimiter=delimiter,
            quotechar=quotechar,
            quoting=quoting,
            restore=restore,
            **kwargs,
        )

    def on(self, query: Query | bool) -> Expression:
        """
        Shadow Table.on.

        Used for joins.

        See Also:
            http://web2py.com/books/default/chapter/29/06/the-database-abstraction-layer?search=export_to_csv_file#One-to-many-relation
        """
        table = self._ensure_table_defined()
        return typing.cast(Expression, table.on(query))

    def with_alias(self: Type[T_MetaInstance], alias: str) -> Type[T_MetaInstance]:
        """
        Shadow Table.with_alias.

        Useful for joins when joining the same table multiple times.

        See Also:
            http://web2py.com/books/default/chapter/29/06/the-database-abstraction-layer#One-to-many-relation
        """
        table = self._ensure_table_defined()
        return typing.cast(Type[T_MetaInstance], table.with_alias(alias))

    def unique_alias(self: Type[T_MetaInstance]) -> Type[T_MetaInstance]:
        """
        Generates a unique alias for this table.

        Useful for joins when joining the same table multiple times
            and you don't want to keep track of aliases yourself.
        """
        key = f"{self.__name__.lower()}_{hash(uuid.uuid4())}"
        return self.with_alias(key)

    # hooks:
    def _hook_once(
        cls: Type[T_MetaInstance], hooks: list[typing.Callable[P, R]], fn: typing.Callable[P, R]
    ) -> Type[T_MetaInstance]:
        @functools.wraps(fn)
        def wraps(*a: P.args, **kw: P.kwargs) -> R:
            try:
                return fn(*a, **kw)
            finally:
                hooks.remove(wraps)

        hooks.append(wraps)
        return cls

    def before_insert(
        cls: Type[T_MetaInstance],
        fn: typing.Callable[[T_MetaInstance], Optional[bool]] | typing.Callable[[OpRow], Optional[bool]],
    ) -> Type[T_MetaInstance]:
        """
        Add a before insert hook.
        """
        if fn not in cls._before_insert:
            cls._before_insert.append(fn)
        return cls

    def before_insert_once(
        cls: Type[T_MetaInstance],
        fn: typing.Callable[[T_MetaInstance], Optional[bool]] | typing.Callable[[OpRow], Optional[bool]],
    ) -> Type[T_MetaInstance]:
        """
        Add a before insert hook that only fires once and then removes itself.
        """
        return cls._hook_once(cls._before_insert, fn)  # type: ignore

    def after_insert(
        cls: Type[T_MetaInstance],
        fn: (
            typing.Callable[[T_MetaInstance, Reference], Optional[bool]]
            | typing.Callable[[OpRow, Reference], Optional[bool]]
        ),
    ) -> Type[T_MetaInstance]:
        """
        Add an after insert hook.
        """
        if fn not in cls._after_insert:
            cls._after_insert.append(fn)
        return cls

    def after_insert_once(
        cls: Type[T_MetaInstance],
        fn: (
            typing.Callable[[T_MetaInstance, Reference], Optional[bool]]
            | typing.Callable[[OpRow, Reference], Optional[bool]]
        ),
    ) -> Type[T_MetaInstance]:
        """
        Add an after insert hook that only fires once and then removes itself.
        """
        return cls._hook_once(cls._after_insert, fn)  # type: ignore

    def before_update(
        cls: Type[T_MetaInstance],
        fn: typing.Callable[[Set, T_MetaInstance], Optional[bool]] | typing.Callable[[Set, OpRow], Optional[bool]],
    ) -> Type[T_MetaInstance]:
        """
        Add a before update hook.
        """
        if fn not in cls._before_update:
            cls._before_update.append(fn)
        return cls

    def before_update_once(
        cls,
        fn: typing.Callable[[Set, T_MetaInstance], Optional[bool]] | typing.Callable[[Set, OpRow], Optional[bool]],
    ) -> Type[T_MetaInstance]:
        """
        Add a before update hook that only fires once and then removes itself.
        """
        return cls._hook_once(cls._before_update, fn)  # type: ignore

    def after_update(
        cls: Type[T_MetaInstance],
        fn: typing.Callable[[Set, T_MetaInstance], Optional[bool]] | typing.Callable[[Set, OpRow], Optional[bool]],
    ) -> Type[T_MetaInstance]:
        """
        Add an after update hook.
        """
        if fn not in cls._after_update:
            cls._after_update.append(fn)
        return cls

    def after_update_once(
        cls: Type[T_MetaInstance],
        fn: typing.Callable[[Set, T_MetaInstance], Optional[bool]] | typing.Callable[[Set, OpRow], Optional[bool]],
    ) -> Type[T_MetaInstance]:
        """
        Add an after update hook that only fires once and then removes itself.
        """
        return cls._hook_once(cls._after_update, fn)  # type: ignore

    def before_delete(cls: Type[T_MetaInstance], fn: typing.Callable[[Set], Optional[bool]]) -> Type[T_MetaInstance]:
        """
        Add a before delete hook.
        """
        if fn not in cls._before_delete:
            cls._before_delete.append(fn)
        return cls

    def before_delete_once(
        cls: Type[T_MetaInstance],
        fn: typing.Callable[[Set], Optional[bool]],
    ) -> Type[T_MetaInstance]:
        """
        Add a before delete hook that only fires once and then removes itself.
        """
        return cls._hook_once(cls._before_delete, fn)

    def after_delete(cls: Type[T_MetaInstance], fn: typing.Callable[[Set], Optional[bool]]) -> Type[T_MetaInstance]:
        """
        Add an after delete hook.
        """
        if fn not in cls._after_delete:
            cls._after_delete.append(fn)
        return cls

    def after_delete_once(
        cls: Type[T_MetaInstance],
        fn: typing.Callable[[Set], Optional[bool]],
    ) -> Type[T_MetaInstance]:
        """
        Add an after delete hook that only fires once and then removes itself.
        """
        return cls._hook_once(cls._after_delete, fn)


class TypedField(Expression, typing.Generic[T_Value]):  # pragma: no cover
    """
    Typed version of pydal.Field, which will be converted to a normal Field in the background.
    """

    # will be set by .bind on db.define
    name = ""
    _db: Optional[pydal.DAL] = None
    _rname: Optional[str] = None
    _table: Optional[Table] = None
    _field: Optional[Field] = None

    _type: T_annotation
    kwargs: Any

    requires: Validator | typing.Iterable[Validator]

    # NOTE: for the logic of converting a TypedField into a pydal Field, see TypeDAL._to_field

    def __init__(
        self,
        _type: Type[T_Value] | types.UnionType = str,  # type: ignore
        /,
        **settings: Unpack[FieldSettings],
    ) -> None:
        """
        Typed version of pydal.Field, which will be converted to a normal Field in the background.

        Provide the Python type for this field as the first positional argument
        and any other settings to Field() as keyword parameters.
        """
        self._type = _type
        self.kwargs = settings
        # super().__init__()

    @typing.overload
    def __get__(self, instance: T_MetaInstance, owner: Type[T_MetaInstance]) -> T_Value:  # pragma: no cover
        """
        row.field -> (actual data).
        """

    @typing.overload
    def __get__(self, instance: None, owner: "Type[TypedTable]") -> "TypedField[T_Value]":  # pragma: no cover
        """
        Table.field -> Field.
        """

    def __get__(
        self, instance: T_MetaInstance | None, owner: Type[T_MetaInstance]
    ) -> typing.Union[T_Value, "TypedField[T_Value]"]:
        """
        Since this class is a Descriptor field, \
            it returns something else depending on if it's called on a class or instance.

        (this is mostly for mypy/typing)
        """
        if instance:
            # this is only reached in a very specific case:
            # an instance of the object was created with a specific set of fields selected (excluding the current one)
            # in that case, no value was stored in the owner -> return None (since the field was not selected)
            return typing.cast(T_Value, None)  # cast as T_Value so mypy understands it for selected fields
        else:
            # getting as class -> return actual field so pydal understands it when using in query etc.
            return typing.cast(TypedField[T_Value], self._field)  # pretend it's still typed for IDE support

    def __str__(self) -> str:
        """
        String representation of a Typed Field.

        If `type` is set explicitly (e.g. TypedField(str, type="text")), that type is used: `TypedField.text`,
        otherwise the type annotation is used (e.g. TypedField(str) -> TypedField.str)
        """
        return str(self._field) if self._field else ""

    def __repr__(self) -> str:
        """
        More detailed string representation of a Typed Field.

        Uses __str__ and adds the provided extra options (kwargs) in the representation.
        """
        s = self.__str__()

        if "type" in self.kwargs:
            # manual type in kwargs supplied
            t = self.kwargs["type"]
        elif issubclass(type, type(self._type)):
            # normal type, str.__name__ = 'str'
            t = getattr(self._type, "__name__", str(self._type))
        elif t_args := typing.get_args(self._type):
            # list[str] -> 'str'
            t = t_args[0].__name__
        else:  # pragma: no cover
            # fallback - something else, may not even happen, I'm not sure
            t = self._type

        s = f"TypedField[{t}].{s}" if s else f"TypedField[{t}]"

        kw = self.kwargs.copy()
        kw.pop("type", None)
        return f"<{s} with options {kw}>"

    def _to_field(self, extra_kwargs: typing.MutableMapping[str, Any]) -> Optional[str]:
        """
        Convert a Typed Field instance to a pydal.Field.

        Actual logic in TypeDAL._to_field but this function creates the pydal type name and updates the kwarg settings.
        """
        other_kwargs = self.kwargs.copy()
        extra_kwargs.update(other_kwargs)  # <- modifies and overwrites the default kwargs with user-specified ones
        return extra_kwargs.pop("type", False) or TypeDAL._annotation_to_pydal_fieldtype(self._type, extra_kwargs)

    def bind(self, field: pydal.objects.Field, table: pydal.objects.Table) -> None:
        """
        Bind the right db/table/field info to this class, so queries can be made using `Class.field == ...`.
        """
        self._table = table
        self._field = field

    def __getattr__(self, key: str) -> Any:
        """
        If the regular getattribute does not work, try to get info from the related Field.
        """
        with contextlib.suppress(AttributeError):
            return super().__getattribute__(key)

        # try on actual field:
        return getattr(self._field, key)

    def __eq__(self, other: Any) -> Query:
        """
        Performing == on a Field will result in a Query.
        """
        return typing.cast(Query, self._field == other)

    def __ne__(self, other: Any) -> Query:
        """
        Performing != on a Field will result in a Query.
        """
        return typing.cast(Query, self._field != other)

    def __gt__(self, other: Any) -> Query:
        """
        Performing > on a Field will result in a Query.
        """
        return typing.cast(Query, self._field > other)

    def __lt__(self, other: Any) -> Query:
        """
        Performing < on a Field will result in a Query.
        """
        return typing.cast(Query, self._field < other)

    def __ge__(self, other: Any) -> Query:
        """
        Performing >= on a Field will result in a Query.
        """
        return typing.cast(Query, self._field >= other)

    def __le__(self, other: Any) -> Query:
        """
        Performing <= on a Field will result in a Query.
        """
        return typing.cast(Query, self._field <= other)

    def __hash__(self) -> int:
        """
        Shadow Field.__hash__.
        """
        return hash(self._field)

    def __invert__(self) -> Expression:
        """
        Performing ~ on a Field will result in an Expression.
        """
        if not self._field:  # pragma: no cover
            raise ValueError("Unbound Field can not be inverted!")

        return typing.cast(Expression, ~self._field)

    def lower(self) -> Expression:
        """
        For string-fields: compare lowercased values.
        """
        if not self._field:  # pragma: no cover
            raise ValueError("Unbound Field can not be lowered!")

        return typing.cast(Expression, self._field.lower())

    # ... etc


class _TypedTable:
    """
    This class is a final shared parent between TypedTable and Mixins.

    This needs to exist because otherwise the __on_define__ of Mixins are not executed.
    Notably, this class exists at a level ABOVE the `metaclass=TableMeta`,
        because otherwise typing gets confused when Mixins are used and multiple types could satisfy
            generic 'T subclass of TypedTable'
        -> Setting 'TypedTable' as the parent for Mixin does not work at runtime (and works semi at type check time)
    """

    id: "TypedField[int]"

    _before_insert: list[typing.Callable[[Self], Optional[bool]] | typing.Callable[[OpRow], Optional[bool]]]
    _after_insert: list[
        typing.Callable[[Self, Reference], Optional[bool]] | typing.Callable[[OpRow, Reference], Optional[bool]]
    ]
    _before_update: list[typing.Callable[[Set, Self], Optional[bool]] | typing.Callable[[Set, OpRow], Optional[bool]]]
    _after_update: list[typing.Callable[[Set, Self], Optional[bool]] | typing.Callable[[Set, OpRow], Optional[bool]]]
    _before_delete: list[typing.Callable[[Set], Optional[bool]]]
    _after_delete: list[typing.Callable[[Set], Optional[bool]]]

    @classmethod
    def __on_define__(cls, db: TypeDAL) -> None:
        """
        Method that can be implemented by tables to do an action after db.define is completed.

        This can be useful if you need to add something like requires=IS_NOT_IN_DB(db, "table.field"),
        where you need a reference to the current database, which may not exist yet when defining the model.
        """

    @classproperty
    def _hooks(cls) -> dict[str, list[typing.Callable[..., Optional[bool]]]]:
        return {
            "before_insert": cls._before_insert,
            "after_insert": cls._after_insert,
            "before_update": cls._before_update,
            "after_update": cls._after_update,
            "before_delete": cls._before_delete,
            "after_delete": cls._after_delete,
        }


class TypedTable(_TypedTable, metaclass=TableMeta):
    """
    Enhanded modeling system on top of pydal's Table that adds typing and additional functionality.
    """

    # set up by 'new':
    _row: Row | None = None

    _with: list[str]

    def _setup_instance_methods(self) -> None:
        self.as_dict = self._as_dict  # type: ignore
        self.__json__ = self.as_json = self._as_json  # type: ignore
        # self.as_yaml = self._as_yaml  # type: ignore
        self.as_xml = self._as_xml  # type: ignore

        self.update = self._update  # type: ignore

        self.delete_record = self._delete_record  # type: ignore
        self.update_record = self._update_record  # type: ignore

    def __new__(
        cls, row_or_id: typing.Union[Row, Query, pydal.objects.Set, int, str, None, "TypedTable"] = None, **filters: Any
    ) -> Self:
        """
        Create a Typed Rows model instance from an existing row, ID or query.

        Examples:
            MyTable(1)
            MyTable(id=1)
            MyTable(MyTable.id == 1)
        """
        table = cls._ensure_table_defined()
        inst = super().__new__(cls)

        if isinstance(row_or_id, TypedTable):
            # existing typed table instance!
            return typing.cast(Self, row_or_id)

        elif isinstance(row_or_id, pydal.objects.Row):
            row = row_or_id
        elif row_or_id is not None:
            row = table(row_or_id, **filters)
        elif filters:
            row = table(**filters)
        else:
            # dummy object
            return inst

        if not row:
            return None  # type: ignore

        inst._row = row

        if hasattr(row, "id"):
            inst.__dict__.update(row)
        else:
            # deal with _extra (and possibly others?)
            # Row <{actual: {}, _extra: ...}>
            inst.__dict__.update(row[str(cls)])

        inst._setup_instance_methods()
        return inst

    def __iter__(self) -> typing.Generator[Any, None, None]:
        """
        Allows looping through the columns.
        """
        row = self._ensure_matching_row()
        yield from iter(row)

    def __getitem__(self, item: str) -> Any:
        """
        Allows dictionary notation to get columns.
        """
        if item in self.__dict__:
            return self.__dict__.get(item)

        # fallback to lookup in row
        if self._row:
            return self._row[item]

        # nothing found!
        raise KeyError(item)

    def __getattr__(self, item: str) -> Any:
        """
        Allows dot notation to get columns.
        """
        if value := self.get(item):
            return value

        raise AttributeError(item)

    def get(self, item: str, default: Any = None) -> Any:
        """
        Try to get a column from this instance, else return default.
        """
        try:
            return self.__getitem__(item)
        except KeyError:
            return default

    def __setitem__(self, key: str, value: Any) -> None:
        """
        Data can both be updated via dot and dict notation.
        """
        return setattr(self, key, value)

    def __int__(self) -> int:
        """
        Calling int on a model instance will return its id.
        """
        return getattr(self, "id", 0)

    def __bool__(self) -> bool:
        """
        If the instance has an underlying row with data, it is truthy.
        """
        return bool(getattr(self, "_row", False))

    def _ensure_matching_row(self) -> Row:
        if not getattr(self, "_row", None):
            raise EnvironmentError("Trying to access non-existant row. Maybe it was deleted or not yet initialized?")
        return self._row

    def __repr__(self) -> str:
        """
        String representation of the model instance.
        """
        model_name = self.__class__.__name__
        model_data = {}

        if self._row:
            model_data = self._row.as_json()

        details = model_name
        details += f"({model_data})"

        if relationships := getattr(self, "_with", []):
            details += f" + {relationships}"

        return f"<{details}>"

    # serialization
    # underscore variants work for class instances (set up by _setup_instance_methods)

    @classmethod
    def as_dict(cls, flat: bool = False, sanitize: bool = True) -> AnyDict:
        """
        Dump the object to a plain dict.

        Can be used as both a class or instance method:
        - dumps the table info if it's a class
        - dumps the row info if it's an instance (see _as_dict)
        """
        table = cls._ensure_table_defined()
        result = table.as_dict(flat, sanitize)
        return typing.cast(AnyDict, result)

    @classmethod
    def as_json(cls, sanitize: bool = True, indent: Optional[int] = None, **kwargs: Any) -> str:
        """
        Dump the object to json.

        Can be used as both a class or instance method:
        - dumps the table info if it's a class
        - dumps the row info if it's an instance (see _as_json)
        """
        data = cls.as_dict(sanitize=sanitize)
        return as_json.encode(data, indent=indent, **kwargs)

    @classmethod
    def as_xml(cls, sanitize: bool = True) -> str:  # pragma: no cover
        """
        Dump the object to xml.

        Can be used as both a class or instance method:
        - dumps the table info if it's a class
        - dumps the row info if it's an instance (see _as_xml)
        """
        table = cls._ensure_table_defined()
        return typing.cast(str, table.as_xml(sanitize))

    @classmethod
    def as_yaml(cls, sanitize: bool = True) -> str:
        """
        Dump the object to yaml.

        Can be used as both a class or instance method:
        - dumps the table info if it's a class
        - dumps the row info if it's an instance (see _as_yaml)
        """
        table = cls._ensure_table_defined()
        return typing.cast(str, table.as_yaml(sanitize))

    def _as_dict(
        self, datetime_to_str: bool = False, custom_types: typing.Iterable[type] | type | None = None
    ) -> AnyDict:
        row = self._ensure_matching_row()

        result = row.as_dict(datetime_to_str=datetime_to_str, custom_types=custom_types)

        def asdict_method(obj: Any) -> Any:  # pragma: no cover
            if hasattr(obj, "_as_dict"):  # typedal
                return obj._as_dict()
            elif hasattr(obj, "as_dict"):  # pydal
                return obj.as_dict()
            else:  # something else??
                return obj.__dict__

        if _with := getattr(self, "_with", None):
            for relationship in _with:
                data = self.get(relationship)

                if isinstance(data, list):
                    data = [asdict_method(_) for _ in data]
                elif data:
                    data = asdict_method(data)

                result[relationship] = data

        return typing.cast(AnyDict, result)

    def _as_json(
        self,
        default: typing.Callable[[Any], Any] = None,
        indent: Optional[int] = None,
        **kwargs: Any,
    ) -> str:
        data = self._as_dict()
        return as_json.encode(data, default=default, indent=indent, **kwargs)

    def _as_xml(self, sanitize: bool = True) -> str:  # pragma: no cover
        row = self._ensure_matching_row()
        return typing.cast(str, row.as_xml(sanitize))

    # def _as_yaml(self, sanitize: bool = True) -> str:
    #     row = self._ensure_matching_row()
    #     return typing.cast(str, row.as_yaml(sanitize))

    def __setattr__(self, key: str, value: Any) -> None:
        """
        When setting a property on a Typed Table model instance, also update the underlying row.
        """
        if self._row and key in self._row.__dict__ and not callable(value):
            # enables `row.key = value; row.update_record()`
            self._row[key] = value

        super().__setattr__(key, value)

    @classmethod
    def update(cls: Type[T_MetaInstance], query: Query, **fields: Any) -> T_MetaInstance | None:
        """
        Update one record.

        Example:
            MyTable.update(MyTable.id == 1, name="NewName") -> MyTable
        """
        # todo: update multiple?
        if record := cls(query):
            return record.update_record(**fields)
        else:
            return None

    def _update(self: T_MetaInstance, **fields: Any) -> T_MetaInstance:
        row = self._ensure_matching_row()
        row.update(**fields)
        self.__dict__.update(**fields)
        return self

    def _update_record(self: T_MetaInstance, **fields: Any) -> T_MetaInstance:
        row = self._ensure_matching_row()
        new_row = row.update_record(**fields)
        self.update(**new_row)
        return self

    def update_record(self: T_MetaInstance, **fields: Any) -> T_MetaInstance:  # pragma: no cover
        """
        Here as a placeholder for _update_record.

        Will be replaced on instance creation!
        """
        return self._update_record(**fields)

    def _delete_record(self) -> int:
        """
        Actual logic in `pydal.helpers.classes.RecordDeleter`.
        """
        row = self._ensure_matching_row()
        result = row.delete_record()
        self.__dict__ = {}  # empty self, since row is no more.
        self._row = None  # just to be sure
        self._setup_instance_methods()
        # ^ instance methods might've been deleted by emptying dict,
        # but we still want .as_dict to show an error, not the table's as_dict.
        return typing.cast(int, result)

    def delete_record(self) -> int:  # pragma: no cover
        """
        Here as a placeholder for _delete_record.

        Will be replaced on instance creation!
        """
        return self._delete_record()

    # __del__ is also called on the end of a scope so don't remove records on every del!!

    # pickling:

    def __getstate__(self) -> AnyDict:
        """
        State to save when pickling.

        Prevents db connection from being pickled.
        Similar to as_dict but without changing the data of the relationships (dill does that recursively)
        """
        row = self._ensure_matching_row()
        result: AnyDict = row.as_dict()

        if _with := getattr(self, "_with", None):
            result["_with"] = _with
            for relationship in _with:
                data = self.get(relationship)

                result[relationship] = data

        result["_row"] = self._row.as_json() if self._row else ""
        return result

    def __setstate__(self, state: AnyDict) -> None:
        """
        Used by dill when loading from a bytestring.
        """
        # as_dict also includes table info, so dump as json to only get the actual row data
        # then create a new (more empty) row object:
        state["_row"] = Row(json.loads(state["_row"]))
        self.__dict__ |= state

    @classmethod
    def _sql(cls) -> str:
        """
        Generate SQL Schema for this table via pydal2sql (if 'migrations' extra is installed).
        """
        try:
            import pydal2sql
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("Can not generate SQL without the 'migration' extra or `pydal2sql` installed!") from e

        return pydal2sql.generate_sql(cls)


# backwards compat:
TypedRow = TypedTable


class TypedRows(typing.Collection[T_MetaInstance], Rows):
    """
    Slighly enhaned and typed functionality on top of pydal Rows (the result of a select).
    """

    records: dict[int, T_MetaInstance]
    # _rows: Rows
    model: Type[T_MetaInstance]
    metadata: Metadata

    # pseudo-properties: actually stored in _rows
    db: TypeDAL
    colnames: list[str]
    fields: list[Field]
    colnames_fields: list[Field]
    response: list[tuple[Any, ...]]

    def __init__(
        self,
        rows: Rows,
        model: Type[T_MetaInstance],
        records: dict[int, T_MetaInstance] = None,
        metadata: Metadata = None,
    ) -> None:
        """
        Should not be called manually!

        Normally, the `records` from an existing `Rows` object are used
            but these can be overwritten with a `records` dict.
        `metadata` can be any (un)structured data
        `model` is a Typed Table class
        """

        def _get_id(row: Row) -> int:
            """
            Try to find the id field in a row.

            If _extra exists, the row changes:
            <Row {'test_relationship': {'id': 1}, '_extra': {'COUNT("test_relationship"."querytable")': 8}}>
            """
            if idx := getattr(row, "id", None):
                return typing.cast(int, idx)
            elif main := getattr(row, str(model), None):
                return typing.cast(int, main.id)
            else:  # pragma: no cover
                raise NotImplementedError(f"`id` could not be found for {row}")

        records = records or {_get_id(row): model(row) for row in rows}
        super().__init__(rows.db, records, rows.colnames, rows.compact, rows.response, rows.fields)
        self.model = model
        self.metadata = metadata or {}
        self.colnames = rows.colnames

    def __len__(self) -> int:
        """
        Return the count of rows.
        """
        return len(self.records)

    def __iter__(self) -> typing.Iterator[T_MetaInstance]:
        """
        Loop through the rows.
        """
        yield from self.records.values()

    def __contains__(self, ind: Any) -> bool:
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
        self, f: typing.Callable[[T_MetaInstance], Query], limitby: tuple[int, int] = None
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

    def exclude(self, f: typing.Callable[[T_MetaInstance], Query]) -> "TypedRows[T_MetaInstance]":
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

    def sort(self, f: typing.Callable[[T_MetaInstance], Any], reverse: bool = False) -> list[T_MetaInstance]:
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
        self, *fields: "str | Field | TypedField[T]", one_result: bool = False, **kwargs: Any
    ) -> dict[T, list[T_MetaInstance]]:
        """
        Group the rows by a specific field (which will be the dict key).
        """
        kwargs["one_result"] = one_result
        result = super().group_by_value(*fields, **kwargs)
        return typing.cast(dict[T, list[T_MetaInstance]], result)

    def as_csv(self) -> str:
        """
        Dump the data to csv.
        """
        return typing.cast(str, super().as_csv())

    def as_dict(
        self,
        key: str = None,
        compact: bool = False,
        storage_to_dict: bool = False,
        datetime_to_str: bool = False,
        custom_types: list[type] = None,
    ) -> dict[int, AnyDict]:
        """
        Get the data in a dict of dicts.
        """
        if any([key, compact, storage_to_dict, datetime_to_str, custom_types]):
            # functionality not guaranteed
            return typing.cast(
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

    def as_json(self, default: typing.Callable[[Any], Any] = None, indent: Optional[int] = None, **kwargs: Any) -> str:
        """
        Turn the data into a dict and then dump to JSON.
        """
        data = self.as_list()

        return as_json.encode(data, default=default, indent=indent, **kwargs)

    def json(self, default: typing.Callable[[Any], Any] = None, indent: Optional[int] = None, **kwargs: Any) -> str:
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
            return typing.cast(list[AnyDict], super().as_list(compact, storage_to_dict, datetime_to_str, custom_types))

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

    def get(self, item: int) -> typing.Optional[T_MetaInstance]:
        """
        Get a row by ID, or receive None if it isn't in this result set.
        """
        return self.records.get(item)

    def update(self, **new_values: Any) -> bool:
        """
        Update the current rows in the database with new_values.
        """
        # cast to make mypy understand .id is a TypedField and not an int!
        table = typing.cast(Type[TypedTable], self.model._ensure_table_defined())

        ids = set(self.column("id"))
        query = table.id.belongs(ids)
        return bool(self.db(query).update(**new_values))

    def delete(self) -> bool:
        """
        Delete the currently selected rows from the database.
        """
        # cast to make mypy understand .id is a TypedField and not an int!
        table = typing.cast(Type[TypedTable], self.model._ensure_table_defined())

        ids = set(self.column("id"))
        query = table.id.belongs(ids)
        return bool(self.db(query).delete())

    def join(
        self,
        field: "Field | TypedField[Any]",
        name: str = None,
        constraint: Query = None,
        fields: list[str | Field] = None,
        orderby: Optional[str | Field] = None,
    ) -> T_MetaInstance:
        """
        This can be used to JOIN with some relationships after the initial select.

        Using the querybuilder's .join() method is prefered!
        """
        result = super().join(field, name, constraint, fields or [], orderby)
        return typing.cast(T_MetaInstance, result)

    def export_to_csv_file(
        self,
        ofile: typing.TextIO,
        null: Any = "<NULL>",
        delimiter: str = ",",
        quotechar: str = '"',
        quoting: int = csv.QUOTE_MINIMAL,
        represent: bool = False,
        colnames: list[str] = None,
        write_colnames: bool = True,
        *args: Any,
        **kwargs: Any,
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
        cls, rows: Rows, model: Type[T_MetaInstance], metadata: Metadata = None
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


from .caching import (  # noqa: E402
    _remove_cache,
    _TypedalCache,
    _TypedalCacheDependency,
    create_and_hash_cache_key,
    get_expire,
    load_from_cache,
    save_to_cache,
)


class QueryBuilder(typing.Generic[T_MetaInstance]):
    """
    Abstration on top of pydal's query system.
    """

    model: Type[T_MetaInstance]
    query: Query
    select_args: list[Any]
    select_kwargs: SelectKwargs
    relationships: dict[str, Relationship[Any]]
    metadata: Metadata

    def __init__(
        self,
        model: Type[T_MetaInstance],
        add_query: Optional[Query] = None,
        select_args: Optional[list[Any]] = None,
        select_kwargs: Optional[SelectKwargs] = None,
        relationships: dict[str, Relationship[Any]] = None,
        metadata: Metadata = None,
    ):
        """
        Normally, you wouldn't manually initialize a QueryBuilder but start using a method on a TypedTable.

        Example:
            MyTable.where(...) -> QueryBuilder[MyTable]
        """
        self.model = model
        table = model._ensure_table_defined()
        default_query = typing.cast(Query, table.id > 0)
        self.query = add_query or default_query
        self.select_args = select_args or []
        self.select_kwargs = select_kwargs or {}
        self.relationships = relationships or {}
        self.metadata = metadata or {}

    def __str__(self) -> str:
        """
        Simple string representation for the query builder.
        """
        return f"QueryBuilder for {self.model}"

    def __repr__(self) -> str:
        """
        Advanced string representation for the query builder.
        """
        return (
            f"<QueryBuilder for {self.model} with "
            f"{len(self.select_args)} select args; "
            f"{len(self.select_kwargs)} select kwargs; "
            f"{len(self.relationships)} relationships; "
            f"query:    {bool(self.query)}; "
            f"metadata: {self.metadata}; "
            f">"
        )

    def __bool__(self) -> bool:
        """
        Querybuilder is truthy if it has any conditions.
        """
        table = self.model._ensure_table_defined()
        default_query = typing.cast(Query, table.id > 0)
        return any(
            [
                self.query != default_query,
                self.select_args,
                self.select_kwargs,
                self.relationships,
                self.metadata,
            ]
        )

    def _extend(
        self,
        add_query: Optional[Query] = None,
        overwrite_query: Optional[Query] = None,
        select_args: Optional[list[Any]] = None,
        select_kwargs: Optional[SelectKwargs] = None,
        relationships: dict[str, Relationship[Any]] = None,
        metadata: Metadata = None,
    ) -> "QueryBuilder[T_MetaInstance]":
        return QueryBuilder(
            self.model,
            (add_query & self.query) if add_query else overwrite_query or self.query,
            (self.select_args + select_args) if select_args else self.select_args,
            (self.select_kwargs | select_kwargs) if select_kwargs else self.select_kwargs,
            (self.relationships | relationships) if relationships else self.relationships,
            (self.metadata | (metadata or {})) if metadata else self.metadata,
        )

    def select(self, *fields: Any, **options: Unpack[SelectKwargs]) -> "QueryBuilder[T_MetaInstance]":
        """
        Fields: database columns by name ('id'), by field reference (table.id) or other (e.g. table.ALL).

        Options:
            paraphrased from the web2py pydal docs,
            For more info, see http://www.web2py.com/books/default/chapter/29/06/the-database-abstraction-layer#orderby-groupby-limitby-distinct-having-orderby_on_limitby-join-left-cache

            orderby: field(s) to order by. Supported:
                table.name - sort by name, ascending
                ~table.name - sort by name, descending
                <random> - sort randomly
                table.name|table.id - sort by two fields (first name, then id)

            groupby, having: together with orderby:
                groupby can be a field (e.g. table.name) to group records by
                having can be a query, only those `having` the condition are grouped

            limitby: tuple of min and max. When using the query builder, .paginate(limit, page) is recommended.
            distinct: bool/field. Only select rows that differ
            orderby_on_limitby (bool, default: True): by default, an implicit orderby is added when doing limitby.
            join: othertable.on(query) - do an INNER JOIN. Using TypeDAL relationships with .join() is recommended!
            left: othertable.on(query) - do a LEFT JOIN. Using TypeDAL relationships with .join() is recommended!
            cache: cache the query result to speed up repeated queries; e.g. (cache=(cache.ram, 3600), cacheable=True)
        """
        return self._extend(select_args=list(fields), select_kwargs=options)

    def where(
        self,
        *queries_or_lambdas: Query | typing.Callable[[Type[T_MetaInstance]], Query],
        **filters: Any,
    ) -> "QueryBuilder[T_MetaInstance]":
        """
        Extend the builder's query.

        Can be used in multiple ways:
        .where(Query) -> with a direct query such as `Table.id == 5`
        .where(lambda table: table.id == 5) -> with a query via a lambda
        .where(id=5) -> via keyword arguments

        When using multiple where's, they will be ANDed:
            .where(lambda table: table.id == 5).where(lambda table: table.id == 6) == (table.id == 5) & (table.id=6)
        When passing multiple queries to a single .where, they will be ORed:
            .where(lambda table: table.id == 5, lambda table: table.id == 6) == (table.id == 5) | (table.id=6)
        """
        new_query = self.query
        table = self.model._ensure_table_defined()

        for field, value in filters.items():
            new_query &= table[field] == value

        subquery: DummyQuery | Query = DummyQuery()
        for query_or_lambda in queries_or_lambdas:
            if isinstance(query_or_lambda, _Query):
                subquery |= typing.cast(Query, query_or_lambda)
            elif callable(query_or_lambda):
                if result := query_or_lambda(self.model):
                    subquery |= result
            elif isinstance(query_or_lambda, (Field, _Field)) or is_typed_field(query_or_lambda):
                subquery |= typing.cast(Query, query_or_lambda != None)
            else:
                raise ValueError(f"Unexpected query type ({type(query_or_lambda)}).")

        if subquery:
            new_query &= subquery

        return self._extend(overwrite_query=new_query)

    def join(
        self,
        *fields: str | Type[TypedTable],
        method: JOIN_OPTIONS = None,
        on: OnQuery | list[Expression] | Expression = None,
        condition: Condition = None,
        condition_and: Condition = None,
    ) -> "QueryBuilder[T_MetaInstance]":
        """
        Include relationship fields in the result.

        `fields` can be names of Relationships on the current model.
        If no fields are passed, all will be used.

        By default, the `method` defined in the relationship is used.
            This can be overwritten with the `method` keyword argument (left or inner)

        `condition_and` can be used to add extra conditions to an inner join.
        """
        # todo: allow limiting amount of related rows returned for join?
        # todo: it would be nice if 'fields' could be an actual relationship
        #   (Article.tags = list[Tag]) and you could change the .condition and .on
        #  this could deprecate condition_and

        relationships = self.model.get_relationships()

        if condition and on:
            raise ValueError("condition and on can not be used together!")
        elif condition:
            if len(fields) != 1:
                raise ValueError("join(field, condition=...) can only be used with exactly one field!")

            if isinstance(condition, pydal.objects.Query):
                condition = as_lambda(condition)

            relationships = {
                str(fields[0]): Relationship(fields[0], condition=condition, join=method, condition_and=condition_and)
            }
        elif on:
            if len(fields) != 1:
                raise ValueError("join(field, on=...) can only be used with exactly one field!")

            if isinstance(on, pydal.objects.Expression):
                on = [on]

            if isinstance(on, list):
                on = as_lambda(on)
            relationships = {str(fields[0]): Relationship(fields[0], on=on, join=method, condition_and=condition_and)}

        else:
            if fields:
                # join on every relationship
                relationships = {str(k): relationships[str(k)].clone(condition_and=condition_and) for k in fields}

            if method:
                relationships = {
                    str(k): r.clone(join=method, condition_and=condition_and) for k, r in relationships.items()
                }

        return self._extend(relationships=relationships)

    def cache(
        self, *deps: Any, expires_at: Optional[dt.datetime] = None, ttl: Optional[int | dt.timedelta] = None
    ) -> "QueryBuilder[T_MetaInstance]":
        """
        Enable caching for this query to load repeated calls from a dill row \
            instead of executing the sql and collecing matching rows again.
        """
        existing = self.metadata.get("cache", {})

        metadata: Metadata = {}

        cache_meta = typing.cast(
            CacheMetadata,
            self.metadata.get("cache", {})
            | {
                "enabled": True,
                "depends_on": existing.get("depends_on", []) + [str(_) for _ in deps],
                "expires_at": get_expire(expires_at=expires_at, ttl=ttl),
            },
        )

        metadata["cache"] = cache_meta
        return self._extend(metadata=metadata)

    def _get_db(self) -> TypeDAL:
        if db := self.model._db:
            return db
        else:  # pragma: no cover
            raise EnvironmentError("@define or db.define is not called on this class yet!")

    def _select_arg_convert(self, arg: Any) -> Any:
        # typedfield are not really used at runtime anymore, but leave it in for safety:
        if isinstance(arg, TypedField):  # pragma: no cover
            arg = arg._field

        return arg

    def delete(self) -> list[int]:
        """
        Based on the current query, delete rows and return a list of deleted IDs.
        """
        db = self._get_db()
        removed_ids = [_.id for _ in db(self.query).select("id")]
        if db(self.query).delete():
            # success!
            return removed_ids

        return []

    def _delete(self) -> str:
        db = self._get_db()
        return str(db(self.query)._delete())

    def update(self, **fields: Any) -> list[int]:
        """
        Based on the current query, update `fields` and return a list of updated IDs.
        """
        # todo: limit?
        db = self._get_db()
        updated_ids = db(self.query).select("id").column("id")
        if db(self.query).update(**fields):
            # success!
            return updated_ids

        return []

    def _update(self, **fields: Any) -> str:
        db = self._get_db()
        return str(db(self.query)._update(**fields))

    def _before_query(self, mut_metadata: Metadata, add_id: bool = True) -> tuple[Query, list[Any], SelectKwargs]:
        select_args = [self._select_arg_convert(_) for _ in self.select_args] or [self.model.ALL]
        select_kwargs = self.select_kwargs.copy()
        query = self.query
        model = self.model
        mut_metadata["query"] = query
        # require at least id of main table:
        select_fields = ", ".join([str(_) for _ in select_args])
        tablename = str(model)

        if add_id and f"{tablename}.id" not in select_fields:
            # fields of other selected, but required ID is missing.
            select_args.append(model.id)

        if self.relationships:
            query, select_args = self._handle_relationships_pre_select(query, select_args, select_kwargs, mut_metadata)

        return query, select_args, select_kwargs

    def to_sql(self, add_id: bool = False) -> str:
        """
        Generate the SQL for the built query.
        """
        db = self._get_db()

        query, select_args, select_kwargs = self._before_query({}, add_id=add_id)

        return str(db(query)._select(*select_args, **select_kwargs))

    def _collect(self) -> str:
        """
        Alias for to_sql, pydal-like syntax.
        """
        return self.to_sql()

    def _collect_cached(self, metadata: Metadata) -> "TypedRows[T_MetaInstance] | None":
        expires_at = metadata["cache"].get("expires_at")
        metadata["cache"] |= {
            # key is partly dependant on cache metadata but not these:
            "key": None,
            "status": None,
            "cached_at": None,
            "expires_at": None,
        }

        _, key = create_and_hash_cache_key(
            self.model,
            metadata,
            self.query,
            self.select_args,
            self.select_kwargs,
            self.relationships.keys(),
        )

        # re-set after creating key:
        metadata["cache"]["expires_at"] = expires_at
        metadata["cache"]["key"] = key

        return load_from_cache(key, self._get_db())

    def execute(self, add_id: bool = False) -> Rows:
        """
        Raw version of .collect which only executes the SQL, without performing any magic afterwards.
        """
        db = self._get_db()
        metadata = typing.cast(Metadata, self.metadata.copy())

        query, select_args, select_kwargs = self._before_query(metadata, add_id=add_id)

        return db(query).select(*select_args, **select_kwargs)

    def collect(
        self, verbose: bool = False, _to: Type["TypedRows[Any]"] = None, add_id: bool = True
    ) -> "TypedRows[T_MetaInstance]":
        """
        Execute the built query and turn it into model instances, while handling relationships.
        """
        if _to is None:
            _to = TypedRows

        db = self._get_db()
        metadata = typing.cast(Metadata, self.metadata.copy())

        if metadata.get("cache", {}).get("enabled") and (result := self._collect_cached(metadata)):
            return result

        query, select_args, select_kwargs = self._before_query(metadata, add_id=add_id)

        metadata["sql"] = db(query)._select(*select_args, **select_kwargs)

        if verbose:  # pragma: no cover
            print(metadata["sql"])

        rows: Rows = db(query).select(*select_args, **select_kwargs)

        metadata["final_query"] = str(query)
        metadata["final_args"] = [str(_) for _ in select_args]
        metadata["final_kwargs"] = select_kwargs

        if verbose:  # pragma: no cover
            print(rows)

        if not self.relationships:
            # easy
            typed_rows = _to.from_rows(rows, self.model, metadata=metadata)

        else:
            # harder: try to match rows to the belonging objects
            # assume structure of {'table': <data>} per row.
            # if that's not the case, return default behavior again
            typed_rows = self._collect_with_relationships(rows, metadata=metadata, _to=_to)

        # only saves if requested in metadata:
        return save_to_cache(typed_rows, rows)

    @typing.overload
    def column(self, field: TypedField[T], **options: Unpack[SelectKwargs]) -> list[T]:
        """
        If a typedfield is passed, the output type can be safely determined.
        """

    @typing.overload
    def column(self, field: T, **options: Unpack[SelectKwargs]) -> list[T]:
        """
        Otherwise, the output type is loosely determined (assumes `field: type` or Any).
        """

    def column(self, field: TypedField[T] | T, **options: Unpack[SelectKwargs]) -> list[T]:
        """
        Get all values in a specific column.

        Shortcut for `.select(field).execute().column(field)`.
        """
        return self.select(field, **options).execute().column(field)

    def _handle_relationships_pre_select(
        self,
        query: Query,
        select_args: list[Any],
        select_kwargs: SelectKwargs,
        metadata: Metadata,
    ) -> tuple[Query, list[Any]]:
        db = self._get_db()
        model = self.model

        metadata["relationships"] = set(self.relationships.keys())

        join = []
        for key, relation in self.relationships.items():
            if not relation.condition or relation.join != "inner":
                continue

            other = relation.get_table(db)
            other = other.with_alias(f"{key}_{hash(relation)}")
            condition = relation.condition(model, other)
            if callable(relation.condition_and):
                condition &= relation.condition_and(model, other)

            join.append(other.on(condition))

        if limitby := select_kwargs.pop("limitby", ()):
            # if limitby + relationships:
            # 1. get IDs of main table entries that match 'query'
            # 2. change query to .belongs(id)
            # 3. add joins etc

            kwargs: SelectKwargs = select_kwargs | {"limitby": limitby}
            # if orderby := select_kwargs.get("orderby"):
            #     kwargs["orderby"] = orderby

            if join:
                kwargs["join"] = join

            ids = db(query)._select(model.id, **kwargs)
            query = model.id.belongs(ids)
            metadata["ids"] = ids

        if join:
            select_kwargs["join"] = join

        left = []

        for key, relation in self.relationships.items():
            other = relation.get_table(db)
            method: JOIN_OPTIONS = relation.join or DEFAULT_JOIN_OPTION

            select_fields = ", ".join([str(_) for _ in select_args])
            pre_alias = str(other)

            if f"{other}." not in select_fields:
                # no fields of other selected. add .ALL:
                select_args.append(other.ALL)
            elif f"{other}.id" not in select_fields:
                # fields of other selected, but required ID is missing.
                select_args.append(other.id)

            if relation.on:
                # if it has a .on, it's always a left join!
                on = relation.on(model, other)
                if not isinstance(on, list):  # pragma: no cover
                    on = [on]

                on = [
                    _
                    for _ in on
                    # only allow Expressions (query and such):
                    if isinstance(_, pydal.objects.Expression)
                ]
                left.extend(on)
            elif method == "left":
                # .on not given, generate it:
                other = other.with_alias(f"{key}_{hash(relation)}")
                condition = typing.cast(Query, relation.condition(model, other))
                if callable(relation.condition_and):
                    condition &= relation.condition_and(model, other)
                left.append(other.on(condition))
            else:
                # else: inner join (handled earlier)
                other = other.with_alias(f"{key}_{hash(relation)}")  # only for replace

            # if no fields of 'other' are included, add other.ALL
            # else: only add other.id if missing
            select_fields = ", ".join([str(_) for _ in select_args])

            post_alias = str(other).split(" AS ")[-1]
            if pre_alias != post_alias:
                # replace .select's with aliased:
                select_fields = select_fields.replace(
                    f"{pre_alias}.",
                    f"{post_alias}.",
                )

                select_args = select_fields.split(", ")

        select_kwargs["left"] = left
        return query, select_args

    def _collect_with_relationships(
        self, rows: Rows, metadata: Metadata, _to: Type["TypedRows[Any]"]
    ) -> "TypedRows[T_MetaInstance]":
        """
        Transform the raw rows into Typed Table model instances.
        """
        db = self._get_db()
        main_table = self.model._ensure_table_defined()

        records = {}
        seen_relations: dict[str, set[str]] = defaultdict(set)  # main id -> set of col + id for relation

        for row in rows:
            main = row[main_table]
            main_id = main.id

            if main_id not in records:
                records[main_id] = self.model(main)
                records[main_id]._with = list(self.relationships.keys())

                # setup up all relationship defaults (once)
                for col, relationship in self.relationships.items():
                    records[main_id][col] = [] if relationship.multiple else None

            # now add other relationship data
            for column, relation in self.relationships.items():
                relationship_column = f"{column}_{hash(relation)}"

                # relationship_column works for aliases with the same target column.
                # if col + relationship not in the row, just use the regular name.

                relation_data = (
                    row[relationship_column] if relationship_column in row else row[relation.get_table_name()]
                )

                if relation_data.id is None:
                    # always skip None ids
                    continue

                if f"{column}-{relation_data.id}" in seen_relations[main_id]:
                    # speed up duplicates
                    continue
                else:
                    seen_relations[main_id].add(f"{column}-{relation_data.id}")

                relation_table = relation.get_table(db)
                # hopefully an instance of a typed table and a regular row otherwise:
                instance = relation_table(relation_data) if looks_like(relation_table, TypedTable) else relation_data

                if relation.multiple:
                    # create list of T
                    if not isinstance(records[main_id].get(column), list):  # pragma: no cover
                        # should already be set up before!
                        setattr(records[main_id], column, [])

                    records[main_id][column].append(instance)
                else:
                    # create single T
                    records[main_id][column] = instance

        return _to(rows, self.model, records, metadata=metadata)

    def collect_or_fail(self, exception: typing.Optional[Exception] = None) -> "TypedRows[T_MetaInstance]":
        """
        Call .collect() and raise an error if nothing found.

        Basically unwraps Optional type.
        """
        if result := self.collect():
            return result

        if not exception:
            exception = ValueError("Nothing found!")

        raise exception

    def __iter__(self) -> typing.Generator[T_MetaInstance, None, None]:
        """
        You can start iterating a Query Builder object before calling collect, for ease of use.
        """
        yield from self.collect()

    def __count(self, db: TypeDAL, distinct: typing.Optional[bool] = None) -> Query:
        # internal, shared logic between .count and ._count
        model = self.model
        query = self.query
        for key, relation in self.relationships.items():
            if (not relation.condition or relation.join != "inner") and not distinct:
                continue

            other = relation.get_table(db)
            if not distinct:
                # todo: can this lead to other issues?
                other = other.with_alias(f"{key}_{hash(relation)}")
            query &= relation.condition(model, other)

        return query

    def count(self, distinct: typing.Optional[bool] = None) -> int:
        """
        Return the amount of rows matching the current query.
        """
        db = self._get_db()
        query = self.__count(db, distinct=distinct)

        return db(query).count(distinct)

    def _count(self, distinct: typing.Optional[bool] = None) -> str:
        """
        Return the SQL for .count().
        """
        db = self._get_db()
        query = self.__count(db, distinct=distinct)

        return typing.cast(str, db(query)._count(distinct))

    def exists(self) -> bool:
        """
        Determines if any records exist matching the current query.

        Returns True if one or more records exist; otherwise, False.

        Returns:
            bool: A boolean indicating whether any records exist.
        """
        return bool(self.count())

    def __paginate(
        self,
        limit: int,
        page: int = 1,
    ) -> "QueryBuilder[T_MetaInstance]":
        available = self.count()

        _from = limit * (page - 1)
        _to = (limit * page) if limit else available

        metadata: Metadata = {}

        metadata["pagination"] = {
            "limit": limit,
            "current_page": page,
            "max_page": math.ceil(available / limit) if limit else 1,
            "rows": available,
            "min_max": (_from, _to),
        }

        return self._extend(select_kwargs={"limitby": (_from, _to)}, metadata=metadata)

    def paginate(self, limit: int, page: int = 1, verbose: bool = False) -> "PaginatedRows[T_MetaInstance]":
        """
        Paginate transforms the more readable `page` and `limit` to pydals internal limit and offset.

        Note: when using relationships, this limit is only applied to the 'main' table and any number of extra rows \
            can be loaded with relationship data!
        """
        builder = self.__paginate(limit, page)

        rows = typing.cast(PaginatedRows[T_MetaInstance], builder.collect(verbose=verbose, _to=PaginatedRows))

        rows._query_builder = builder
        return rows

    def _paginate(
        self,
        limit: int,
        page: int = 1,
    ) -> str:
        builder = self.__paginate(limit, page)
        return builder._collect()

    def chunk(self, chunk_size: int) -> typing.Generator["TypedRows[T_MetaInstance]", Any, None]:
        """
        Generator that yields rows from a paginated source in chunks.

        This function retrieves rows from a paginated data source in chunks of the
        specified `chunk_size` and yields them as TypedRows.

        Example:
            ```
            for chunk_of_rows in Table.where(SomeTable.id > 5).chunk(100):
                for row in chunk_of_rows:
                    # Process each row within the chunk.
                    pass
            ```
        """
        page = 1

        while rows := self.__paginate(chunk_size, page).collect():
            yield rows
            page += 1

    def first(self, verbose: bool = False) -> T_MetaInstance | None:
        """
        Get the first row matching the currently built query.

        Also adds paginate, since it would be a waste to select more rows than needed.
        """
        if row := self.paginate(page=1, limit=1, verbose=verbose).first():
            return self.model.from_row(row)
        else:
            return None

    def _first(self) -> str:
        return self._paginate(page=1, limit=1)

    def first_or_fail(self, exception: typing.Optional[Exception] = None, verbose: bool = False) -> T_MetaInstance:
        """
        Call .first() and raise an error if nothing found.

        Basically unwraps Optional type.
        """
        if inst := self.first(verbose=verbose):
            return inst

        if not exception:
            exception = ValueError("Nothing found!")

        raise exception


S = typing.TypeVar("S")


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

    def next(self) -> Self:
        """
        Get the next page.
        """
        data = self.metadata["pagination"]
        if data["current_page"] >= data["max_page"]:
            raise StopIteration("Final Page")

        return self._query_builder.paginate(limit=data["limit"], page=data["current_page"] + 1)

    def previous(self) -> Self:
        """
        Get the previous page.
        """
        data = self.metadata["pagination"]
        if data["current_page"] <= 1:
            raise StopIteration("First Page")

        return self._query_builder.paginate(limit=data["limit"], page=data["current_page"] - 1)

    def as_dict(self, *_: Any, **__: Any) -> PaginateDict:  # type: ignore
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

    def count(self, distinct: typing.Optional[bool] = None, cache: AnyDict = None) -> int:
        """
        Count returns an int.
        """
        result = super().count(distinct, cache)
        return typing.cast(int, result)

    def select(self, *fields: Any, **attributes: Any) -> TypedRows[T_MetaInstance]:
        """
        Select returns a TypedRows of a user defined table.

        Example:
            result: TypedRows[MyTable] = db(MyTable.id > 0).select()

            for row in result:
                reveal_type(row)  # MyTable
        """
        rows = super().select(*fields, **attributes)
        return typing.cast(TypedRows[T_MetaInstance], rows)
