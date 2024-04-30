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

    def __init_subclass__(cls, slug_field: str = None, slug_suffix_length: int = 0, **kw: Any) -> None:
        """
        Bind 'slug field' option to be used later (on_define).

        You can control the length of the random suffix with the `slug_suffix_length` option (0 is no suffix).
        """
        # unfortunately, PyCharm and mypy do not recognize/autocomplete/typecheck init subclass (keyword) arguments.
        if slug_field is None:
            raise ValueError(
                "SlugMixin requires a valid slug_field setting: "
                "e.g. `class MyClass(TypedTable, SlugMixin, slug_field='title'): ...`"
            )

        if "slug_suffix" in kw:
            warnings.warn(
                "The 'slug_suffix' option is deprecated, use 'slug_suffix_length' instead.",
                DeprecationWarning,
            )

        slug_suffix = slug_suffix_length or kw.get("slug_suffix", 0)

        cls.__settings__ = {
            "slug_field": slug_field,
            "slug_suffix": slug_suffix,
        }

    @classmethod
    def __on_define__(cls, db: TypeDAL) -> None:
        """
        When db is available, include a before_insert hook to generate and include a slug.
        """
        super().__on_define__(db)

        # slugs should not be editable (for SEO reasons), so there is only a before insert hook:
        def generate_slug_before_insert(row: OpRow) -> None:
            settings = cls.__settings__

            text_input = row[settings["slug_field"]]
            generated_slug = slugify(text_input)

            if suffix_len := settings["slug_suffix"]:
                generated_slug += f"-{slug_random_suffix(suffix_len)}"

            row["slug"] = slugify(generated_slug)

        cls._before_insert.append(generate_slug_before_insert)

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
