import typing
from uuid import uuid4

from src.typedal import Relationship, TypeDAL, TypedField, TypedTable, relationship

db = TypeDAL("sqlite:memory")
# db = TypeDAL("sqlite://debug.db")


class TaggableMixin:
    tags = relationship(list["Tag"], lambda cls: (Tagged.entity == cls.gid) & (Tagged.tag == Tag.id))
    # tags = relationship(list["Tag"], tagged)


@db.define()
class Role(TypedTable, TaggableMixin):
    name: str
    users = relationship(list["User"], lambda table: User.roles.belongs(table.id))


@db.define()
class User(TypedTable, TaggableMixin):
    gid = TypedField(str, default=uuid4)
    name: str
    roles: TypedField[list[Role]]

    # relationships:
    articles: Relationship[list["Article"]]  # defaults to looking up 'reference User'


@db.define()
class Article(TypedTable, TaggableMixin):
    gid = TypedField(str, default=uuid4)
    title: str
    author: User  # == relationship(User, 'User.gid') # stores User.gid in `_author` field


@db.define()
class Tag(TypedTable):
    gid = TypedField(str, default=uuid4)
    name: str

    articles = relationship(list[Article], lambda cls: (Tagged.tag == cls.id) & (Article.gid == Tagged.entity))

    users = relationship(list[User], lambda cls: (Tagged.tag == cls.id) & (User.gid == Tagged.entity))


@db.define()
class Tagged(TypedTable):  # pivot table
    entity: str  # any gid
    tag: Tag


# todo: relationships (has one, ...) with other key than ID
# todo: hasOneThrough?


def _setup_data():
    # clean up
    for table in db.tables:
        db[table].truncate()

    # roles
    roles = ["reader", "writer", "editor"]
    reader, writer, editor = Role.bulk_insert([{"name": _} for _ in roles])

    # users

    reader, writer, editor = User.bulk_insert(
        [
            {"name": "Reader 1", "roles": [reader]},
            {"name": "Writer 1", "roles": [reader, writer]},
            {"name": "Editor 1", "roles": [reader, writer, editor]},
        ]
    )

    # articles

    article1, article2 = Article.bulk_insert(
        [
            {"title": "Article 1", "author": writer},
            {"title": "Article 2", "author": editor},
        ]
    )

    # tags

    tag_draft, tag_published, tag_breaking, tag_trending, tag_offtopic = Tag.bulk_insert(
        [
            {"name": "draft"},
            {"name": "published"},
            {"name": "breaking-news"},
            {"name": "trending"},
            {"name": "off-topic"},
        ]
    )

    # tagged

    Tagged.bulk_insert(
        [
            # entities
            {"entity": article1.gid, "tag": tag_draft},
            {"entity": article1.gid, "tag": tag_offtopic},
            {"entity": article2.gid, "tag": tag_published},
            {"entity": article2.gid, "tag": tag_breaking},
            # users
            {"entity": writer.gid, "tag": tag_trending},
            # tags
            {"entity": tag_offtopic.gid, "tag": tag_draft},
        ]
    )

    db.commit()


def test_pydal_way():
    _setup_data()

    # hasOne: from article to author
    row = db((db.article.title == "Article 1") & (db.article.author == db.user.id)).select().first()  # inner join

    # or:
    article = db.article(title="Article 1")
    author = db.user(article.author)

    assert row.user.name == "Writer 1" == author.name

    # belongsTo: from author to article
    row = db((db.user.name == "Writer 1") & (db.article.author == db.user.id)).select().first()  # inner join

    # or:
    author = db.user(name="Writer 1")
    article = db.article(author=author)

    assert row.article.title == "Article 1" == article.title


def test_typedal_way():
    _setup_data()

    article = Article.where(title="Article 1").first_or_fail()

    # ???
    print(article.author)  # int object (id)
    print(article.tags)  # Relationship object

    # hasOne
    article = Article.where(title="Article 1").join("author", "tags").first_or_fail()

    assert isinstance(article, Article)

    assert article.title == "Article 1"
    assert article.author.name == "Writer 1"

    assert isinstance(article.author, User)

    assert len(article.tags) == 2  # draft, offtopic

    tag = article.tags[0]
    assert tag.name
    assert isinstance(tag, Tag)

    # belongsTo

    # ...

    # todo: author without articles etc
    #
    # reader = User.where(name="Reader 1").join().first()
    #
    # assert reader
    # assert not reader.articles
    # assert not reader.tags
