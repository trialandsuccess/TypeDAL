import uuid

from src.typedal import TypeDAL, TypedTable
from src.typedal.fields import StringField

db = TypeDAL('sqlite:memory:')


@db.define
class User(TypedTable):
    name: str
    gid = StringField(default=uuid.uuid4)


class Post(TypedTable):
    title: str
    gid = StringField(default=uuid.uuid4)


db.define(Post)


@db.define()
class Tag(TypedTable):
    slug: str
    gid = StringField(default=uuid.uuid4)


@db.define()
class Tagged(TypedTable):
    entity: str  # uuid
    tag: Tag


def test_orm_classes():
    henkie = User.insert(
        name="Henkie"
    )

    ijsjes = Post.insert(
        title="IJsjes"
    )

    post_by_henkie = Tag.insert(
        slug='post-by-henkie'
    )

    melk_producten = Tag.insert(
        slug='melk-producten'
    )

    Tagged.insert(
        entity=henkie.gid,
        tag=post_by_henkie
    )

    Tagged.insert(
        entity=ijsjes.gid,
        tag=melk_producten
    )

    print(Tagged.select(Tagged.ALL).where(Tagged.id).first())
    print(list(Tagged.where(Tagged.id).select(Tagged.ALL)))
