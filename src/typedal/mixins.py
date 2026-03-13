"""
This file contains example Mixins.

Mixins can add reusable fields and behavior (optimally both, otherwise it doesn't add much).
"""

import base64
import datetime as dt
import os
import types
import typing as t
import warnings

from pydal import DAL
from pydal.validators import IS_NOT_IN_DB, ValidationError
from slugify import slugify

from .core import TypeDAL
from .fields import DatetimeField, StringField, TypedField, is_typed_field
from .helpers import all_dict, filter_out
from .relationships import Relationship, resolve_relationship_type
from .tables import TableMeta, TypedTable, _TypedTable
from .types import OpRow, Set, T_MetaInstance


class Mixin(_TypedTable, metaclass=TableMeta):
    """
    A mixin should be derived from this class.

    The mixin base class itself doesn't do anything,
    but using it makes sure the mixin fields are placed AFTER the table's normal fields (instead of before)

    During runtime, mixin should not inherit from TypedTable to prevent MRO issues
        ('inconsistent method resolution' or 'metaclass conflicts')
    """

    __settings__: t.ClassVar[dict[str, t.Any]]

    def __init_subclass__(cls, **kwargs: t.Any):
        """
        Ensures __settings__ exists for other mixins.
        """
        cls.__settings__ = getattr(cls, "__settings__", None) or {}


class TimestampsMixin(Mixin):
    """
    A Mixin class for adding timestamp fields to a model.
    """

    created_at = DatetimeField(default=dt.datetime.now, writable=False)
    updated_at = DatetimeField(default=dt.datetime.now, writable=False)

    @classmethod
    def __on_define__(cls, db: TypeDAL) -> None:
        """
        Hook called when defining the model to initialize timestamps.

        Args:
            db (TypeDAL): The database layer.
        """
        super().__on_define__(db)

        def set_updated_at(_: Set, row: OpRow) -> None:
            """
            Callback function to update the 'updated_at' field before saving changes.

            Args:
                _: Set: Unused parameter.
                row (OpRow): The row to update.
            """
            row["updated_at"] = dt.datetime.now()

        cls._before_update.append(set_updated_at)


def slug_random_suffix(length: int = 8) -> str:
    """
    Generate a random suffix to make slugs unique, even when titles are the same.

    UUID4 uses 16 bytes, but 8 is probably more than enough given you probably don't have THAT much duplicate titles.
    Strip away '=' to make it URL-safe
        (even though 'urlsafe_b64encode' sounds like it should already be url-safe - it is not)
    """
    return base64.urlsafe_b64encode(os.urandom(length)).rstrip(b"=").decode().strip("=")


# noinspection PyPep8Naming
class HAS_UNIQUE_SLUG(IS_NOT_IN_DB):
    """
    Checks if slugified field is already in the db.

    Usage:
        table.name = HAS_UNIQUE_SLUG(db, "table.slug")
    """

    def __init__(
        self,
        db: TypeDAL | DAL,
        field: str,  # table.slug
        error_message: str = "This slug is not unique: %s.",
    ):
        """
        Based on IS_NOT_IN_DB but with less options and a different default error message.
        """
        super().__init__(db, field, error_message)

    def validate[T](self, original: T, record_id: t.Optional[int] = None) -> T:
        """
        Performs checks to see if the slug already exists for a different row.
        """
        value = slugify(str(original))
        if not value.strip():
            raise ValidationError(self.translator(self.error_message))

        tablename, fieldname = str(self.field).split(".")
        table = self.dbset.db[tablename]
        field = table[fieldname]
        query = field == value

        # make sure exclude the record_id
        row_id = record_id or self.record_id
        if isinstance(row_id, dict):  # pragma: no cover
            row_id = table(**row_id)
        if row_id is not None:
            query &= table._id != row_id
        subset = self.dbset(query)

        if subset.count():
            raise ValidationError(self.error_message % value)

        return original


class SlugMixin(Mixin):
    """
    (Opinionated) example mixin to add a 'slug' field, which depends on a user-provided other field.

    Some random bytes are added at the end to prevent duplicates.

    Example:
        >>> class MyTable(TypedTable, SlugMixin, slug_field="some_name", slug_suffix_length=8):
        >>>    some_name: str
        >>>    ...
    """

    # pub:
    slug = StringField(unique=True, writable=False)
    # priv:
    __settings__: t.TypedDict(  # type: ignore
        "SlugFieldSettings",
        {
            "slug_field": str,
            "slug_suffix": int,
        },
    )  # set via init subclass

    def __init_subclass__(
        cls,
        slug_field: t.Optional[str] = None,
        slug_suffix_length: int = 0,
        slug_suffix: t.Optional[int] = None,
        **kw: t.Any,
    ) -> None:
        """
        Bind 'slug field' option to be used later (on_define).

        You can control the length of the random suffix with the `slug_suffix_length` option (0 is no suffix).
        """
        super().__init_subclass__(**kw)

        # unfortunately, PyCharm and mypy do not recognize/autocomplete/typecheck init subclass (keyword) arguments.
        if slug_field is None:
            raise ValueError(
                "SlugMixin requires a valid slug_field setting: "
                "e.g. `class MyClass(TypedTable, SlugMixin, slug_field='title'): ...`",
            )

        if slug_suffix:
            warnings.warn(
                "The 'slug_suffix' option is deprecated, use 'slug_suffix_length' instead.",
                DeprecationWarning,
            )

        slug_suffix = slug_suffix_length or kw.get("slug_suffix", 0)

        # append settings:
        cls.__settings__["slug_field"] = slug_field
        cls.__settings__["slug_suffix"] = slug_suffix

    @classmethod
    def __generate_slug_before_insert(cls, row: OpRow) -> None:
        if row.get("slug"):  # type: ignore
            # manually set -> skip
            return None

        settings = cls.__settings__

        text_input = row[settings["slug_field"]]
        generated_slug = slugify(text_input)

        if suffix_len := settings["slug_suffix"]:
            generated_slug += f"-{slug_random_suffix(suffix_len)}"

        row["slug"] = slugify(generated_slug)
        return None

    @classmethod
    def __on_define__(cls, db: TypeDAL) -> None:
        """
        When db is available, include a before_insert hook to generate and include a slug.
        """
        super().__on_define__(db)
        settings = cls.__settings__

        # slugs should not be editable (for SEO reasons), so there is only a before insert hook:
        cls._before_insert.append(cls.__generate_slug_before_insert)

        if settings["slug_suffix"] == 0:
            # add a validator to the field that will be slugified:
            slug_field = getattr(cls, settings["slug_field"])
            current_requires = getattr(slug_field, "requires", None) or []
            if not isinstance(current_requires, list):
                current_requires = [current_requires]

            current_requires.append(HAS_UNIQUE_SLUG(db, f"{cls}.slug"))

            slug_field.requires = current_requires

    @classmethod
    def from_slug(cls: t.Type[T_MetaInstance], slug: str, join: bool = True) -> t.Optional[T_MetaInstance]:
        """
        Find a row by its slug.
        """
        builder = cls.where(slug=slug)
        if join:
            builder = builder.join()

        return builder.first()

    @classmethod
    def from_slug_or_fail(cls: t.Type[T_MetaInstance], slug: str, join: bool = True) -> T_MetaInstance:
        """
        Find a row by its slug, or raise an error if it doesn't exist.
        """
        builder = cls.where(slug=slug)
        if join:
            builder = builder.join()

        return builder.first_or_fail()


@t.runtime_checkable
class BaseModeProtocol(t.Protocol):
    """Minimal protocol for pydantic-compatible objects."""

    def model_dump(self, mode: str = "python", **kwargs: t.Any) -> dict[str, t.Any]:
        """Return a serialized mapping representation."""


try:
    from pydantic import BaseModel
except ImportError:
    BaseModel = BaseModeProtocol  # type: ignore


def dump_pydantic[T](values: T, _shallow_nested: bool = False) -> T:
    """Recursively convert pydantic-like values into plain JSON-compatible structures."""
    if isinstance(values, PydanticMixin):
        return values.model_dump(mode="json", _shallow=_shallow_nested)  # type: ignore
    elif isinstance(values, BaseModel):
        return values.model_dump(mode="json")  # type: ignore
    elif callable(getattr(values, "as_dict", None)):
        return dump_pydantic(values.as_dict(), _shallow_nested)  # type: ignore
    elif callable(getattr(values, "as_list", None)):
        return dump_pydantic(values.as_list(), _shallow_nested)  # type: ignore
    elif isinstance(values, dict):
        return {k: dump_pydantic(v, _shallow_nested) for k, v in values.items()}  # type: ignore
    elif isinstance(values, (list, set, tuple)):
        return [dump_pydantic(value, _shallow_nested) for value in values]  # type: ignore
    else:
        return values


class PydanticMixin(Mixin):
    """Mixin that provides pydantic schema generation and dumping helpers."""

    @classmethod
    def _ensure_pydantic_compatible_type(
        cls,
        field_name: str,
        field_type: t.Any,
    ) -> None:
        origin = t.get_origin(field_type)
        args = t.get_args(field_type)

        if origin is not None:
            for arg in args:
                cls._ensure_pydantic_compatible_type(field_name, arg)
            return

        if not isinstance(field_type, type):
            return

        is_typedal_model = issubclass(field_type, TypedTable)
        has_pydantic_hook = getattr(field_type, "__get_pydantic_core_schema__", None)

        if is_typedal_model and not has_pydantic_hook:
            raise ValueError(
                f"{cls.__name__}.{field_name} references "
                f"{field_type.__name__}, but {field_type.__name__} is not "
                f"Pydantic-compatible. Add PydanticMixin to that model too.",
            )

    @classmethod
    def _pydantic_fields(
        cls,
        *,
        include_relationships: bool = False,
        include_properties: bool = False,
    ) -> dict[str, t.Any]:
        annotations: dict[str, t.Any] = {
            field_name: field_type
            for field_name, field_type in t.get_type_hints(cls, include_extras=True).items()
            if not field_name.startswith("_")
        }

        full_dict = all_dict(cls)

        relationship_fields = cls._typedal_collect_relationship_fields(full_dict)
        relationship_names = set(relationship_fields)

        property_names = {
            field_name
            for field_name, field_value in full_dict.items()
            if not field_name.startswith("_") and isinstance(field_value, property)
        }

        if not include_relationships:
            for field_name in relationship_names:
                annotations.pop(field_name, None)

        if not include_properties:
            for field_name in property_names:
                annotations.pop(field_name, None)

        for field_name, field_value in full_dict.items():
            if field_name.startswith("_"):
                continue

            if is_typed_field(field_value):
                annotations.setdefault(field_name, field_value._type)

        if include_relationships:
            for field_name, field_value in relationship_fields.items():
                annotations.setdefault(field_name, field_value)

        if include_properties:
            for field_name, field_value in full_dict.items():
                if field_name.startswith("_"):
                    continue

                if not isinstance(field_value, property):
                    continue

                getter = field_value.fget
                if getter is None:
                    continue

                property_hints = t.get_type_hints(getter, include_extras=True)
                return_type = property_hints.get("return")
                if return_type is not None:
                    annotations[field_name] = return_type

        fields = {
            field_name: cls._unwrap_pydantic_field_type(field_type) for field_name, field_type in annotations.items()
        }

        for field_name, field_type in fields.items():
            cls._ensure_pydantic_compatible_type(field_name, field_type)

        return fields

    @classmethod
    def _unwrap_pydantic_field_type(cls, field_type: t.Any) -> t.Any:
        if t.get_origin(field_type) is TypedField:
            return t.get_args(field_type)[0]
        return field_type

    @classmethod
    def _typedal_collect_relationship_fields(
        cls,
        full_dict: dict[str, t.Any],
    ) -> dict[str, t.Any]:
        relationship_fields: dict[str, t.Any] = {}

        for field_name, relationship_value in filter_out(full_dict, Relationship).items():
            relationship_type = cls._typedal_resolve_relationship_python_type(
                relationship_value=relationship_value,
            )
            if relationship_type is not None:
                relationship_fields[field_name] = relationship_type

        return relationship_fields

    @classmethod
    def _typedal_resolve_relationship_python_type(
        cls,
        relationship_value: t.Any,
    ) -> t.Any | None:
        relationship_type = getattr(relationship_value, "_type", None)
        if relationship_type is None:
            return None

        known_classes = db._known_classes() if (db := getattr(cls, "_db", None)) else {}

        return resolve_relationship_type(
            relationship_type,
            namespace=known_classes,
        )

    @staticmethod
    def _make_instance_converter(_: type, fields: dict[str, t.Any]) -> t.Callable[[t.Any], t.Any]:
        _PRIMITIVES = (str, float, bool, bytes)

        def convert(value: t.Any) -> t.Any:
            if isinstance(value, dict):
                return value
            if isinstance(value, int):
                # Raw foreign-key integer — represent as minimal record with just the id
                return {"id": value}
            if isinstance(value, _PRIMITIVES) or value is None:
                return value
            # Handles both TypedTable instances and raw pydal Row objects
            return {k: getattr(value, k, None) for k in fields}

        return convert

    @classmethod
    def _field_core_schema(
        cls,
        field_type: t.Any,
        handler: t.Any,
    ) -> t.Any:
        from pydantic_core import core_schema

        origin = t.get_origin(field_type)
        args = t.get_args(field_type)

        if origin is None:
            if (
                isinstance(field_type, type)
                and issubclass(field_type, TypedTable)
                and issubclass(field_type, PydanticMixin)
            ):
                shallow_fields = field_type._pydantic_fields(
                    include_relationships=False,
                    include_properties=False,
                )
                return core_schema.no_info_before_validator_function(
                    cls._make_instance_converter(field_type, shallow_fields),
                    field_type._typed_dict_schema(handler=handler, _fields=shallow_fields),
                )

            return handler.generate_schema(field_type)

        # Lists/sets/frozensets are handled explicitly; other generics fall back to pydantic.
        sequence_builders = {
            list: core_schema.list_schema,
            set: core_schema.set_schema,
            frozenset: core_schema.frozenset_schema,
        }
        if origin in sequence_builders:
            item_type = args[0] if args else t.Any
            return sequence_builders[origin](cls._field_core_schema(item_type, handler))

        if isinstance(origin, type) and issubclass(origin, t.Mapping):
            key_type = args[0] if len(args) > 0 else t.Any
            value_type = args[1] if len(args) > 1 else t.Any
            return core_schema.dict_schema(
                keys_schema=cls._field_core_schema(key_type, handler),
                values_schema=cls._field_core_schema(value_type, handler),
            )

        if origin is tuple:
            if len(args) == 2 and args[1] is Ellipsis:
                return core_schema.tuple_variable_schema(
                    cls._field_core_schema(args[0], handler),
                )
            return core_schema.tuple_schema(
                [cls._field_core_schema(item_type, handler) for item_type in args],
            )

        if origin in (t.Union, types.UnionType):
            non_none_args = [arg for arg in args if arg is not type(None)]
            has_none = len(non_none_args) != len(args)

            if len(non_none_args) == 1 and has_none:
                return core_schema.nullable_schema(cls._field_core_schema(non_none_args[0], handler))

            return core_schema.union_schema([cls._field_core_schema(arg, handler) for arg in args])

        return handler.generate_schema(field_type)

    @classmethod
    def _typed_dict_schema(
        cls,
        handler: t.Any,
        *,
        include_relationships: bool = False,
        include_properties: bool = False,
        _fields: dict[str, t.Any] | None = None,
        _required_fields: set[str] = frozenset(),  # type: ignore
    ) -> t.Any:
        from pydantic_core import core_schema

        is_shallow = _fields is not None
        fields = (
            _fields
            if is_shallow
            else cls._pydantic_fields(
                include_relationships=include_relationships,
                include_properties=include_properties,
            )
        ) or {}

        def make_field(field_name: str, field_type: t.Any) -> t.Any:
            inner = cls._field_core_schema(field_type, handler)
            if field_name in _required_fields:
                # Always computed — keep required and non-nullable for clean TS types
                return core_schema.typed_dict_field(inner, required=True)
            # DB fields / relationships: TypeDAL can return partial rows, so allow None
            return core_schema.typed_dict_field(
                core_schema.with_default_schema(core_schema.nullable_schema(inner), default=None),
                required=False,
            )

        schema_fields = {field_name: make_field(field_name, field_type) for field_name, field_type in fields.items()}

        # Shallow (nested) schemas are inlined — no $defs entry, no ugly suffix in OpenAPI.
        # Full schemas use the clean class name so OpenAPI shows "Song", not "Song_rel_True...".
        ref = None if is_shallow else f"{cls.__module__}.{cls.__qualname__}"

        return core_schema.typed_dict_schema(schema_fields, ref=ref)

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        source_type: t.Any,
        handler: t.Any,
    ) -> t.Any:
        """Build the pydantic-core schema for this TypeDAL model."""
        from pydantic_core import core_schema

        fields = cls._pydantic_fields(
            include_relationships=True,
            include_properties=True,
        )

        # Properties are always computed — they must stay required + non-nullable
        # so the generated TypeScript types don't become `string | null | undefined`.
        full_dict = all_dict(cls)
        property_names = {
            name for name, val in full_dict.items() if not name.startswith("_") and isinstance(val, property)
        }

        return core_schema.no_info_before_validator_function(
            cls._make_instance_converter(cls, fields),
            cls._typed_dict_schema(
                handler=handler,
                include_relationships=True,
                include_properties=True,
                _required_fields=property_names,
            ),
        )

    @classmethod
    def __get_pydantic_json_schema__(
        cls,
        schema: t.Any,
        handler: t.Any,
    ) -> dict[str, t.Any]:
        """Build the JSON schema by delegating to pydantic's handler."""
        return handler(schema)  # type: ignore

    def model_dump(self, mode: str = "python", *, _shallow: bool = False) -> dict[str, t.Any]:
        """Serialize this model to a dict, with optional shallow nested output."""
        cls = type(self)
        data: dict[str, t.Any] = {}
        for field_name in self._pydantic_fields(
            include_relationships=not _shallow,
            include_properties=not _shallow,
        ):
            # Match web2py/pyDAL behavior: unreadable db fields are excluded from serialized output.
            model_attr = getattr(cls, field_name, None)
            if hasattr(model_attr, "readable") and not getattr(model_attr, "readable"):
                continue

            data[field_name] = getattr(self, field_name, None)

        if mode == "json":
            return dump_pydantic(data, _shallow_nested=True)

        return data
