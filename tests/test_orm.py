import typing
import uuid
from collections import ChainMap

from typing_extensions import reveal_type

from src.typedal.core import TypeDAL, TypedField, TypedTable

T_MetaInstance = typing.TypeVar("T_MetaInstance")


def _all_annotations(cls: type) -> ChainMap[str, type]:
    """
    Returns a dictionary-like ChainMap that includes annotations for all \
    attributes defined in cls or inherited from superclasses.
    """
    return ChainMap(*(c.__annotations__ for c in getattr(cls, "__mro__", []) if "__annotations__" in c.__dict__))


def all_annotations(cls: type, _except: typing.Optional[typing.Iterable[str]] = None) -> dict[str, type]:
    """
    Wrapper around `_all_annotations` that filters away any keys in _except.

    It also flattens the ChainMap to a regular dict.
    """
    if _except is None:
        _except = set()

    _all = _all_annotations(cls)
    return {k: v for k, v in _all.items() if k not in _except}


T_Table = typing.TypeVar("T_Table", bound=TypedTable)

TypeTable = typing.Type[T_Table]

# class TypeDAL:
#     tables: list[typing.Type[TypedTable]]
#
#     def __init__(self, conn):
#         self.tables = []
#
#     @typing.overload
#     def define(self, table: TypeTable) -> TypeTable:
#         ...
#
#     @typing.overload
#     def define(self, table: None = None) -> typing.Callable[[TypeTable], TypeTable]:
#         ...
#
#     def define(
#         self, table: typing.Optional[TypeTable] = None
#     ) -> TypeTable | typing.Callable[[TypeTable], TypeTable]:
#         if table:  # and issubclass(table, Table)
#             self.tables.append(table)
#             table._db = self
#             return table
#         else:
#             # called with ()
#             def wrapper(table: TypeTable) -> TypeTable:
#                 return self.define(table)
#
#             return wrapper


T_Value = typing.TypeVar("T_Value")  # actual type of the Field (via Generic)

# T_Table = typing.TypeVar("T_Table")  # typevar used by __get__


###

db = TypeDAL("sqlite:memory:")


@db.define
class User(TypedTable):
    name: TypedField[str]
    gid = TypedField(str, default=uuid.uuid4)
    age = TypedField(int, default=0)

    # def __init__(self) -> None:
    #     self.__dict__.update(
    #         {
    #             "id": 3,
    #             "name": "Steve",
    #             "gid": "11-22-33",
    #             "age": 69,
    #         }
    #     )


class Post(TypedTable):
    title: str
    gid = TypedField(default=uuid.uuid4)


db.define(Post)


@db.define()
class Tag(TypedTable):
    slug: TypedField[str]
    gid = TypedField(default=uuid.uuid4)


@db.define()
class Tagged(TypedTable):
    entity: str  # uuid
    tag: Tag


def test_types() -> None:
    user = User.insert(name="Steve")
    reveal_type(User.id)
    reveal_type(user.id)

    reveal_type(User.name)
    reveal_type(user.name)

    reveal_type(User.gid)
    reveal_type(user.gid)

    reveal_type(User.age)
    reveal_type(user.age)

    user.delete_record()


def test_orm_classes():
    henkie = User.insert(name="Henkie")

    assert isinstance(henkie, User)
    assert henkie.name == "Henkie"

    row = db(db.user.name == "Henkie").select().first()
    assert User.from_row(row).name == "Henkie"

    ijsjes = Post.insert(title="IJsjes")

    sql = Tag._insert(slug="post-by-henkie")
    assert sql
    assert isinstance(sql, str)

    post_by_henkie = Tag.insert(slug="post-by-henkie")

    melk_producten = Tag.insert(slug="melk-producten")

    Tagged.insert(entity=henkie.gid, tag=post_by_henkie)

    Tagged.insert(entity=ijsjes.gid, tag=melk_producten)

    first = Tagged.select(Tagged.ALL).where(Tagged.id).first()
    assert first

    multiple = list(Tagged.where(Tagged.id).select(Tagged.ALL))
    assert len(multiple) == 2

    other_methods = list(Tag.where(Tag.slug.belongs(["post-by-henkie", "unknown"])))

    assert len(other_methods) == 1


if __name__ == "__main__":
    test_types()
    test_orm_classes()
