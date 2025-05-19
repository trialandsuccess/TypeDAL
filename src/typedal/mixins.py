"""
This file contains example Mixins.

Mixins can add reusable fields and behavior (optimally both, otherwise it doesn't add much).
"""

import base64
import os
import typing
import warnings
from datetime import datetime
from typing import Any, Optional

from pydal import DAL
from pydal.validators import IS_NOT_IN_DB, ValidationError
from slugify import slugify

from .core import (  # noqa F401 - used by example in docstring
    QueryBuilder,
    T_MetaInstance,
    TableMeta,
    TypeDAL,
    TypedTable,
    _TypedTable,
)
from .fields import DatetimeField, StringField
from .types import OpRow, Set


class Mixin(_TypedTable):
    """
    A mixin should be derived from this class.

    The mixin base class itself doesn't do anything,
    but using it makes sure the mixin fields are placed AFTER the table's normal fields (instead of before)

    During runtime, mixin should not have a base class in order to prevent MRO issues
        ('inconsistent method resolution' or 'metaclass conflicts')
    """

    __settings__: typing.ClassVar[dict[str, Any]]

    def __init_subclass__(cls, **kwargs: Any):
        """
        Ensures __settings__ exists for other mixins.
        """
        cls.__settings__ = getattr(cls, "__settings__", None) or {}


class TimestampsMixin(Mixin):
    """
    A Mixin class for adding timestamp fields to a model.
    """

    created_at = DatetimeField(default=datetime.now, writable=False)
    updated_at = DatetimeField(default=datetime.now, writable=False)

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
            row["updated_at"] = datetime.now()

        cls._before_update.append(set_updated_at)


def slug_random_suffix(length: int = 8) -> str:
    """
    Generate a random suffix to make slugs unique, even when titles are the same.

    UUID4 uses 16 bytes, but 8 is probably more than enough given you probably don't have THAT much duplicate titles.
    Strip away '=' to make it URL-safe
        (even though 'urlsafe_b64encode' sounds like it should already be url-safe - it is not)
    """
    return base64.urlsafe_b64encode(os.urandom(length)).rstrip(b"=").decode().strip("=")


T = typing.TypeVar("T")


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

    def validate(self, original: T, record_id: Optional[int] = None) -> T:
        """
        Performs checks to see if the slug already exists for a different row.
        """
        value = slugify(str(original))
        if not value.strip():
            raise ValidationError(self.translator(self.error_message))

        (tablename, fieldname) = str(self.field).split(".")
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
    __settings__: typing.TypedDict(  # type: ignore
        "SlugFieldSettings",
        {
            "slug_field": str,
            "slug_suffix": int,
        },
    )  # set via init subclass

    def __init_subclass__(
        cls,
        slug_field: typing.Optional[str] = None,
        slug_suffix_length: int = 0,
        slug_suffix: Optional[int] = None,
        **kw: Any,
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
                "e.g. `class MyClass(TypedTable, SlugMixin, slug_field='title'): ...`"
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
    def from_slug(cls: typing.Type[T_MetaInstance], slug: str, join: bool = True) -> Optional[T_MetaInstance]:
        """
        Find a row by its slug.
        """
        builder = cls.where(slug=slug)
        if join:
            builder = builder.join()

        return builder.first()

    @classmethod
    def from_slug_or_fail(cls: typing.Type[T_MetaInstance], slug: str, join: bool = True) -> T_MetaInstance:
        """
        Find a row by its slug, or raise an error if it doesn't exist.
        """
        builder = cls.where(slug=slug)
        if join:
            builder = builder.join()

        return builder.first_or_fail()
