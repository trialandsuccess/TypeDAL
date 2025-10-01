"""
Contains base functionality related to Relationships.
"""

import inspect
import typing as t
import warnings

import pydal.objects

from .constants import JOIN_OPTIONS
from .core import TypeDAL
from .fields import TypedField
from .helpers import extract_type_optional, looks_like, unwrap_type
from .types import Condition, OnQuery, T_Field

To_Type = t.TypeVar("To_Type")


class Relationship(t.Generic[To_Type]):
    """
    Define a relationship to another table.
    """

    _type: t.Type[To_Type]
    table: t.Type["TypedTable"] | type | str
    condition: Condition
    condition_and: Condition
    on: OnQuery
    multiple: bool
    join: JOIN_OPTIONS
    nested: dict[str, t.Self]

    def __init__(
        self,
        _type: t.Type[To_Type],
        condition: Condition = None,
        join: JOIN_OPTIONS = None,
        on: OnQuery = None,
        condition_and: Condition = None,
        nested: dict[str, t.Self] = None,
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

        if args := t.get_args(_type):
            self.table = unwrap_type(args[0])
            self.multiple = True
        else:
            self.table = t.cast(type[TypedTable], _type)
            self.multiple = False

        if isinstance(self.table, str):
            self.table = TypeDAL.to_snake(self.table)

        self.nested = nested or {}

    def clone(self, **update: t.Any) -> "Relationship[To_Type]":
        """
        Create a copy of the relationship, possibly updated.
        """
        return self.__class__(
            update.get("_type") or self._type,
            update.get("condition") or self.condition,
            update.get("join") or self.join,
            update.get("on") or self.on,
            update.get("condition_and") or self.condition_and,
            (self.nested | extra) if (extra := update.get("nested")) else self.nested,  # type: ignore
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

    def __get__(self, instance: t.Any, owner: t.Any) -> "t.Optional[list[t.Any]] | Relationship[To_Type]":
        """
        Relationship is a descriptor class, which can be returned from a class but not an instance.

        For an instance, using .join() will replace the Relationship with the actual data.
        If you forgot to join, a warning will be shown and empty data will be returned.
        """
        if not instance:
            # relationship queried on class, that's allowed
            return self

        warnings.warn(
            "Trying to get data from a relationship object! Did you forget to join it?",
            category=RuntimeWarning,
        )
        if self.multiple:
            return []
        else:
            return None


def relationship(
    _type: t.Type[To_Type],
    condition: Condition = None,
    join: JOIN_OPTIONS = None,
    on: OnQuery = None,
) -> To_Type:
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
    return t.cast(
        # note: The descriptor `Relationship[To_Type]` is more correct, but pycharm doesn't really get that.
        # so for ease of use, just cast to the refered type for now!
        # e.g. x = relationship(Author) -> x: Author
        To_Type,
        Relationship(_type, condition, join, on),
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
