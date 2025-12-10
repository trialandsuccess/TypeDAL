"""
Contains base functionality related to Relationships.
"""

import inspect
import typing as t
import warnings

import pydal.objects

from .config import LazyPolicy
from .constants import JOIN_OPTIONS
from .core import TypeDAL
from .fields import TypedField
from .helpers import extract_type_optional, looks_like, unwrap_type
from .types import Condition, OnQuery, T_Field

To_Type = t.TypeVar("To_Type")


# default lazy policy is defined at the TypeDAL() instance settings level


class Relationship(t.Generic[To_Type]):
    """
    Define a relationship to another table.
    """

    _type: t.Type[To_Type]
    table: t.Type["TypedTable"] | type | str  # use get_table() to resolve later on
    condition: Condition
    condition_and: Condition
    on: OnQuery
    multiple: bool
    join: JOIN_OPTIONS
    _lazy: LazyPolicy | None
    nested: dict[str, t.Self]
    explicit: bool
    name: str | None = None  # set by __set_name__

    def __init__(
        self,
        _type: t.Type[To_Type],
        condition: Condition = None,
        join: JOIN_OPTIONS = None,
        on: OnQuery = None,
        condition_and: Condition = None,
        nested: dict[str, t.Self] = None,
        lazy: LazyPolicy | None = None,
        explicit: bool = False,
    ):
        """
        Should not be called directly, use relationship() instead!
        """
        if condition and on:
            raise self._error_duplicate_condition(condition, on)

        self._type = _type
        self.condition = condition
        self.join = "left" if on else join  # .on is always left join!
        self.on = on
        self.condition_and = condition_and
        self._lazy = lazy

        if args := t.get_args(_type):
            self.table = unwrap_type(args[0])
            self.multiple = True
        else:
            self.table = t.cast(type[TypedTable], _type)
            self.multiple = False

        if isinstance(self.table, str):
            self.table = TypeDAL.to_snake(self.table)

        self.explicit = explicit
        self.nested = nested or {}

    def clone(self, **update: t.Any) -> "Relationship[To_Type]":
        """
        Create a copy of the relationship, possibly updated.
        """
        condition = update.get("condition")
        on = update.get("on")

        if on and condition:  # pragma: no cover
            raise self._error_duplicate_condition(condition, on)

        return self.__class__(
            update.get("_type") or self._type,
            None if on else (condition or self.condition),
            update.get("join") or self.join,
            None if condition else (on or self.on),
            update.get("condition_and") or self.condition_and,
            (self.nested | extra) if (extra := update.get("nested")) else self.nested,  # type: ignore
            update.get("lazy") or self._lazy,
        )

    @staticmethod
    def _error_duplicate_condition(condition: Condition, on: OnQuery) -> t.Never:
        warnings.warn(f"Relation | Both specified! {condition=} {on=}")
        raise ValueError("Please specify either a condition or an 'on' statement for this relationship!")

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
        lazy_str = f" lazy={self.lazy}" if self.lazy != "warn" else ""
        return f"<Relationship{join}{lazy_str} {src_code}>"

    def __set_name__(self, owner: t.Type["TypedTable"], name: str) -> None:
        """Called automatically when assigned to a class attribute."""
        self.name = name

    def get_table(self, db: "TypeDAL") -> t.Type["TypedTable"]:
        """
        Get the table this relationship is bound to.
        """
        table = self.table  # can be a string because db wasn't available yet

        if isinstance(table, str):
            if mapped := db._class_map.get(table):
                # yay
                return mapped

            # boo, fall back to untyped table but pretend it is typed:
            return t.cast(t.Type["TypedTable"], db[table])  # eh close enough!

        return table

    def get_db(self) -> TypeDAL | None:
        """
        Retrieves the database instance associated with the table.

        Returns:
            TypeDAL | None: The database instance if it exists, or None otherwise.
        """
        return getattr(self.table, "_db", None)

    @property
    def lazy(self) -> LazyPolicy:
        """
        Gets the lazy policy configured in the current context.

        The method first checks for a customized lazy policy for this relationship.
        If not found, it attempts to retrieve the lazy policy from the database.
        If neither option is available, it returns a conservative fallback value.

        Returns:
            LazyPolicy or str: The configured lazy policy or a fallback value.
        """
        if customized := self._lazy:
            return customized

        if db := self.get_db():
            return db._config.lazy_policy

        # conservative fallback:
        return "warn"

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

    def __get__(
        self,
        instance: "TypedTable",
        owner: t.Type["TypedTable"],
    ) -> "t.Optional[list[t.Any]] | Relationship[To_Type]":
        """
        Relationship is a descriptor class, which can be returned from a class but not an instance.

        For an instance, using .join() will replace the Relationship with the actual data.
        Behavior when accessed without joining depends on the lazy policy.
        """
        if not instance:
            # relationship queried on class, that's allowed
            return self

        # instance: TypedTable instance
        # owner: TypedTable class

        if not self.name:  # pragma: no cover
            raise ValueError("Relationship does not seem to be connected to a table field.")

        # Handle different lazy policies
        if self.lazy == "forbid":  # pragma: no cover
            raise AttributeError(
                f"Accessing relationship '{self.name}' without joining is forbidden. "
                f"Use .join('{self.name}') in your query or set lazy='allow' if this is intentional.",
            )

        fallback_value: t.Optional[list[t.Any]] = [] if self.multiple else None

        if self.lazy == "ignore":  # pragma: no cover
            # Return empty silently
            return fallback_value

        if self.lazy == "warn":
            # Warn and return empty
            warnings.warn(
                f"Trying to access relationship '{self.name}' without joining. "
                f"Did you forget to use .join('{self.name}')? Returning empty value.",
                category=RuntimeWarning,
            )
            return fallback_value

        # For "tolerate" and "allow", we fetch the data
        try:
            resolved_table = self.get_table(instance._db)

            builder = owner.where(id=instance.id).join(self.name)
            if issubclass(resolved_table, TypedTable) or isinstance(resolved_table, pydal.objects.Table):
                # is a table so we can select ALL and ignore non-required fields of parent row:
                builder = builder.select(owner.id, resolved_table.ALL)

            if self.lazy == "tolerate":
                warnings.warn(
                    f"Lazy loading relationship '{self.name}'. "
                    "This performs an extra database query. "
                    f"Consider using .join('{self.name}') for better performance.",
                    category=RuntimeWarning,
                )

            return builder.first()[self.name]  # type: ignore
        except Exception as e:  # pragma: no cover
            warnings.warn(
                f"Failed to lazy load relationship '{self.name}': {e}",
                category=RuntimeWarning,
                source=e,
            )

            return fallback_value


@t.overload
def relationship(
    _type: type[list[To_Type]],
    condition: Condition = None,
    join: JOIN_OPTIONS = None,
    on: OnQuery = None,
    lazy: LazyPolicy | None = None,
    explicit: bool = False,
) -> list[To_Type]:
    """
    Define a relationship that returns a list of related instances.

    Args:
        _type: A list type hint like list[Office] to indicate multiple related records.

    Returns:
        A list of related instances.
    """


@t.overload
def relationship(
    _type: t.Type[To_Type] | str,
    condition: Condition = None,
    *,
    join: t.Literal["inner"],
    on: OnQuery = None,
    lazy: LazyPolicy | None = None,
    explicit: bool = False,
) -> To_Type:
    """
    Define a relationship that returns a single related instance (never None with inner join).

    Args:
        _type: A type or string reference like City to indicate a single related record.
        join: Set to 'inner' to guarantee a non-null result.

    Returns:
        A single related instance (guaranteed non-null with inner join).
    """


@t.overload
def relationship(
    _type: t.Type[To_Type] | str,
    condition: Condition = None,
    join: JOIN_OPTIONS = None,
    on: OnQuery = None,
    lazy: LazyPolicy | None = None,
    explicit: bool = False,
) -> To_Type | None:
    """
    Define a relationship that returns a single optional related instance.

    Args:
        _type: A type or string reference like City to indicate a single related record.

    Returns:
        A single related instance or None.
    """


def relationship(
    _type: type[list[To_Type]] | t.Type[To_Type] | str,
    condition: Condition = None,
    join: JOIN_OPTIONS = None,
    on: OnQuery = None,
    lazy: LazyPolicy | None = None,
    explicit: bool = False,
) -> list[To_Type] | To_Type | None:
    """
    Define a relationship to another table, when its id is not stored in the current table.

    Args:
        _type: The type of the related table. Use list[Type] for one-to-many relationships.
        condition: Lambda function defining the join condition between tables.
                   Example: lambda self, post: self.id == post.author
        join: Join strategy ('left', 'inner', etc.). Defaults to 'left' when using 'on'.
              When 'inner' is used with a single type, the result is guaranteed non-null.
        on: Alternative to condition for complex queries with pivot tables.
            Allows specifying multiple join conditions to avoid cross joins.
        lazy: Controls behavior when accessing relationship data without explicitly joining:
            - "forbid": Raise an error (strictest, prevents N+1 queries)
            - "warn": Return empty value with warning
            - "ignore": Return empty value silently
            - "tolerate": Fetch data with warning (convenient but warns about performance)
            - "allow": Fetch data silently (most permissive, use only for known cheap queries)
        explicit: If True, this relationship is only joined when explicitly requested
                  (e.g. User.join("tags")). Bare User.join() calls will skip it.
                  Useful for expensive or rarely-needed relationships. Defaults to False.

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

    For complex queries with a pivot table, 'on' can be set instead of 'condition':
        class User(TypedTable):
        ...

        tags = relationship(list["Tag"], on=lambda self, tag: [
                Tagged.on(Tagged.entity == entity.gid),
                Tag.on((Tagged.tag == tag.id)),
            ])

    If you'd try to capture this in a single 'condition', pydal would create a cross join which is much less efficient.
    """
    return t.cast(
        # note: The descriptor `Relationship[To_Type]` is more correct, but pycharm doesn't really get that.
        # so for ease of use, just cast to the refered type for now!
        # e.g. x = relationship(Author) -> x: Author
        To_Type,
        Relationship(_type, condition, join, on, lazy=lazy, explicit=explicit),  # type: ignore
    )


def _generate_relationship_condition(_: t.Type["TypedTable"], key: str, field: T_Field) -> Condition:
    origin = t.get_origin(field)
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
    cls: t.Type["TypedTable"] | type[t.Any],
    key: str,
    field: T_Field,
) -> t.Optional[Relationship[t.Any]]:
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
        if args := t.get_args(field):
            # TypedField[SomeType] -> SomeType
            field = args[0]
        elif hasattr(field, "_type"):
            # TypedField(SomeType) -> SomeType
            field = t.cast(T_Field, field._type)
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

    join = "left" if optional or t.get_origin(field) is list else "inner"

    return Relationship(t.cast(type[TypedTable], field), condition, t.cast(JOIN_OPTIONS, join))


# note: these imports exist at the bottom of this file to prevent circular import issues:

from .tables import TypedTable  # noqa: E402
