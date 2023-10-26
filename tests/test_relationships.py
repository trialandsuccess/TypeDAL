import types
import typing
from uuid import uuid4

import pytest

from src.typedal import Relationship, TypeDAL, TypedField, TypedTable, relationship
from src.typedal.core import to_relationship

db = TypeDAL("sqlite:memory")
# db = TypeDAL("sqlite://debug.db")


class TaggableMixin:
    tags = relationship(
        list["Tag"],
        # lambda self, _: (Tagged.entity == self.gid) & (Tagged.tag == Tag.id)
        # doing an .on with and & inside can lead to a cross join,
        # for relationships with pivot tables a manual on query is prefered:
        on=lambda entity, _tag: [
            Tagged.on(Tagged.entity == entity.gid),
            Tag.on((Tagged.tag == Tag.id)),
        ],
    )
    # tags = relationship(list["Tag"], tagged)


@db.define()
class Role(TypedTable, TaggableMixin):
    name: str
    users = relationship(list["User"], lambda self, other: other.roles.contains(self.id))


@db.define()
class User(TypedTable, TaggableMixin):
    gid = TypedField(str, default=uuid4)
    name: str
    roles: TypedField[list[Role]]

    # relationships:
    articles = relationship(list["Article"], lambda self, other: other.author == self.id)

    # one-to-one
    bestie = relationship("BestFriend", lambda _user, _bestie: _user.id == _bestie.friend)


@db.define()
class BestFriend(TypedTable):
    name: str
    friend: User


@db.define()
class Article(TypedTable, TaggableMixin):
    gid = TypedField(str, default=uuid4)
    title: str
    author: User  # auto relationship
    secondary_author: typing.Optional[User]  # auto relationship but optional
    final_editor: User | None  # auto relationship but optional


@db.define()
class Tag(TypedTable):
    gid = TypedField(str, default=uuid4)
    name: str

    articles = relationship(list[Article], lambda self, other: (Tagged.tag == self.id) & (other.gid == Tagged.entity))
    users = relationship(list[User], lambda self, other: (Tagged.tag == self.id) & (other.gid == Tagged.entity))


@db.define()
class Tagged(TypedTable):  # pivot table
    entity: str  # any gid
    tag: Tag


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
            {"title": "Article 1", "author": writer, "final_editor": editor},
            {"title": "Article 2", "author": editor, "secondary_author": editor},
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

    BestFriend.insert(friend=reader, name="Reader's Bestie")

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

    # user through article: 1 - many

    all_articles = Article.join().collect().as_dict()

    assert all_articles[1]["final_editor"]["name"] == "Editor 1"
    assert all_articles[2]["secondary_author"]["name"] == "Editor 1"

    assert all_articles[1]["secondary_author"] is None
    assert all_articles[2]["final_editor"] is None

    article1 = Article.where(title="Article 1").first_or_fail()
    article2 = Article.where(title="Article 2").first_or_fail()

    assert isinstance(article1.author, int)
    assert isinstance(article2.author, int)
    assert isinstance(article1.final_editor, int)
    assert isinstance(article2.final_editor, types.NoneType)

    with pytest.warns(RuntimeWarning):
        assert article1.tags == []

    with pytest.warns(RuntimeWarning):
        assert article2.tags == []

    articles1 = Article.where(title="Article 1").join().first_or_fail()

    assert articles1.final_editor.name == "Editor 1"

    articles2 = (
        Article.where(title="Article 1").join("author", method="inner").join("tags", method="left").first_or_fail()
    )
    articles3 = Article.where(title="Article 1").join("author", "tags").first_or_fail()

    for article in [articles1, articles2, articles3]:
        assert isinstance(article, Article)

        assert article.title == "Article 1"
        assert article.author.name == "Writer 1"

        assert isinstance(article.author, User)

        assert len(article.tags) == 2  # draft, offtopic

        tag = article.tags[0]
        assert tag.name
        assert isinstance(tag, Tag)

    # reverse: user to articles
    user = User.where(name="Writer 1").join("articles").first_or_fail()

    assert user
    assert len(user.articles) == 1

    # 1 - 1 (user <-> friend and reverse)

    non_joined_user = User.collect().first()

    with pytest.warns(RuntimeWarning):
        assert non_joined_user.bestie is None

    users = User.join().collect()

    assert len(users) == 3  # reader, writer, editor

    # get by id:
    reader = users[1]
    writer = users[2]
    editor = users[3]

    bestie = BestFriend.where(id=reader.bestie.id).join("friend").first()
    assert reader.bestie.name == "Reader's Bestie" == bestie.name
    assert bestie.friend.name == reader.name

    assert "+ ['friend']" in repr(bestie)

    assert not writer.bestie
    assert not editor.bestie

    assert len(reader.roles) == 1
    assert len(writer.roles) == 2
    assert len(editor.roles) == 3

    assert len(reader.tags) == 0
    assert len(writer.tags) == 1
    assert len(editor.tags) == 0

    # articles are main writer only:
    assert len(reader.articles) == 0
    assert len(writer.articles) == 1
    assert len(editor.articles) == 1

    # tag to articles and users (many-to-many, through 'tagged'):
    tags = Tag.join().collect()

    assert len(tags) == 5

    for tag in tags:
        # every tag is used exactly once in this dataset
        assert (len(tag.users) + len(tag.articles)) == 1

    # from role to users: BelongsToMany via list:reference

    role_writer = Role.where(Role.name == "writer").join().first_or_fail()

    assert len(role_writer.users) == 2


def test_reprs():
    assert "Relationship:left on=" in repr(Article.tags)

    article = Article.first()

    with pytest.warns(RuntimeWarning):
        assert repr(article.tags) == "[]"

    empty = Relationship(Article)

    assert empty.get_table_name() == "article"

    assert "missing condition" in repr(empty)

    empty = Relationship("article")

    assert empty.get_table(db) == Article

    db.define_table("new")
    empty = Relationship("new")
    assert empty.get_table(db) == db.new

    assert empty.get_table_name() == "new"

    empty = Relationship(db.new)
    assert empty.get_table(db) == db.new

    assert empty.get_table_name() == "new"


def test_illegal():
    with pytest.raises(ValueError), pytest.warns(UserWarning):

        class HasRelationship:
            something = relationship("...", condition=lambda: 1, on=lambda: 2)
