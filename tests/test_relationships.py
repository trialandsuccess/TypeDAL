import time
import types
import typing
from uuid import uuid4

import pytest

from src.typedal import Relationship, TypeDAL, TypedField, TypedTable, relationship
from src.typedal.caching import (
    _TypedalCache,
    _TypedalCacheDependency,
    clear_cache,
    clear_expired,
    remove_cache,
)

db = TypeDAL("sqlite:memory")


# db = TypeDAL("sqlite://debug.db")


class TaggableMixin:
    tags = relationship(
        list["Tag"],
        # lambda self, _: (Tagged.entity == self.gid) & (Tagged.tag == Tag.id)
        # doing an .on with and & inside can lead to a cross join,
        # for relationships with pivot tables a manual on query with aliases is prefered:
        on=lambda entity, tag: [
            tagged := Tagged.unique_alias(),
            tagged.on(tagged.entity == entity.gid),
            tag.on((tagged.tag == tag.id)),
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
    name: TypedField[str]
    roles: TypedField[list[Role]]
    main_role = TypedField(Role)
    extra_roles = TypedField(list[Role])

    # relationships:
    articles = relationship(list["Article"], lambda self, other: other.author == self.id)

    # one-to-one
    bestie: Relationship["BestFriend"] = relationship("BestFriend", lambda _user, _bestie: _user.id == _bestie.friend)


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


@db.define()
class Empty(TypedTable): ...


def _setup_data():
    # clean up
    for table in db.tables:
        db[table].truncate()

    db._timings.clear()

    # roles
    roles = ["reader", "writer", "editor"]
    reader, writer, editor = Role.bulk_insert([{"name": _} for _ in roles])

    # users

    reader, writer, editor = User.bulk_insert(
        [
            {"name": "Reader 1", "roles": [reader], "main_role": reader, "extra_roles": []},
            {"name": "Writer 1", "roles": [reader, writer], "main_role": writer, "extra_roles": []},
            {"name": "Editor 1", "roles": [reader, writer, editor], "main_role": editor, "extra_roles": []},
        ]
    )

    # no relationships:
    new_author = User.insert(name="Untagged Author", roles=[], main_role=writer, extra_roles=[])
    untagged1 = Article.insert(title="Untagged Article 1", author=new_author)
    untagged2 = Article.insert(title="Untagged Article 2", author=new_author)

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

    with pytest.raises(ValueError):
        Empty.first_or_fail()

    # user through article: 1 - many
    all_articles = Article.join().collect().as_dict()

    assert all_articles[3]["final_editor"]["name"] == "Editor 1"
    assert all_articles[4]["secondary_author"]["name"] == "Editor 1"

    assert all_articles[3]["secondary_author"] is None
    assert all_articles[4]["final_editor"] is None

    assert Article.first_or_fail()

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

    assert len(users) == 4  # reader, writer, editor, untagged

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
    _setup_data()
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

    relation = Article.join("author").relationships["author"]

    assert "AND" not in repr(relation)

    relation = Article.join("author", condition_and=lambda article, author: author.name != "Hank").relationships[
        "author"
    ]

    assert "AND" in repr(relation) and "Hank" in repr(relation)


@db.define()
class CacheFirst(TypedTable):
    name: str


@db.define(cache_dependency=False)
class NoCacheSecond(TypedTable):
    name: str


@db.define()
class CacheTwoRelationships(TypedTable):
    first: CacheFirst
    second: NoCacheSecond


def test_relationship_detection():
    user_table_relationships = User.get_relationships()

    assert user_table_relationships["roles"]
    assert user_table_relationships["main_role"]
    assert user_table_relationships["extra_roles"]
    assert user_table_relationships["articles"]
    assert user_table_relationships["bestie"]
    assert user_table_relationships["tags"]

    assert user_table_relationships["roles"].join == "left"
    assert user_table_relationships["main_role"].join == "inner"
    assert user_table_relationships["extra_roles"].join == "left"


def test_join_with_different_condition():
    _setup_data()

    role_with_users = Role.join(
        "users",
        method="inner",
    ).first()

    assert role_with_users.users
    assert role_with_users.users[0].name == "Reader 1"

    role_with_users = Role.join(
        "users", method="inner", condition_and=lambda role, user: ~user.name.like("Reader%")
    ).first()

    assert role_with_users.users
    assert role_with_users.users[0].name != "Reader 1"

    # left:
    role_with_users = Role.join(
        "users", method="left", condition_and=lambda role, user: ~user.name.like("Reader%")
    ).first()

    assert role_with_users.users
    assert role_with_users.users[0].name != "Reader 1"


def test_caching():
    uncached = User.join().collect_or_fail()
    cached = User.cache().join().collect_or_fail()  # not actually cached yet!
    cached_user_only = User.join().cache(User.id).collect_or_fail()  # idem

    assert uncached.as_json() == cached.as_json()

    assert not uncached.metadata.get("cache", {}).get("enabled")
    assert cached.metadata.get("cache", {}).get("enabled")
    assert cached_user_only.metadata.get("cache", {}).get("enabled")

    assert not uncached.metadata.get("cache", {}).get("depends_on")
    assert not cached.metadata.get("cache", {}).get("depends_on")
    assert cached_user_only.metadata.get("cache", {}).get("depends_on")

    assert uncached.metadata.get("cache", {}).get("status") != "cached"
    assert cached.metadata.get("cache", {}).get("status") != "cached"
    assert cached_user_only.metadata.get("cache", {}).get("status") != "cached"

    assert uncached.as_dict() == cached.as_dict() == cached_user_only.as_dict()

    # assert not uncached.metadata.get("cached_at")
    # assert not cached.metadata.get("cached_at")
    # assert not cached_user_only.metadata.get("cached_at")

    uncached2 = User.join().collect_or_fail()
    cached2 = User.cache().join().collect_or_fail()
    cached_user_only2 = User.join().cache(User.id).collect_or_fail()

    assert (
        len(uncached2)
        == len(uncached)
        == len(cached2)
        == len(cached)
        == len(cached_user_only2)
        == len(cached_user_only)
    )

    assert uncached.as_json() == uncached2.as_json() == cached.as_json() == cached2.as_json()

    assert cached.first().gid == cached2.first().gid

    assert (
        [_.name for _ in uncached2.first().roles]
        == [_.name for _ in cached.first().roles]
        == [_.name for _ in cached2.first().roles]
    )

    assert not uncached2.metadata.get("cache", {}).get("enabled")
    assert cached2.metadata.get("cache", {}).get("enabled")
    assert cached_user_only2.metadata.get("cache", {}).get("enabled")

    assert not uncached2.metadata.get("cache", {}).get("depends_on")
    assert not cached2.metadata.get("cache", {}).get("depends_on")
    assert cached_user_only2.metadata.get("cache", {}).get("depends_on")

    assert uncached2.metadata.get("cache", {}).get("status") != "cached"
    assert cached2.metadata.get("cache", {}).get("status") == "cached"
    assert cached_user_only2.metadata.get("cache", {}).get("status") == "cached"

    # now lets update (and invalidate) something
    Role.where(name="reader").update(name="new-reader")

    uncached3 = User.join().collect_or_fail()
    cached3 = User.cache().join().collect_or_fail()
    cached_user_only3 = User.join().cache(User.id).collect_or_fail()

    assert "new-reader" in {_.name for _ in uncached3.first().roles}
    assert "new-reader" in {_.name for _ in cached3.first().roles}  # should be dropped by dependency
    assert "new-reader" not in {_.name for _ in cached_user_only3.first().roles}  # still old value

    assert uncached3.metadata.get("cache", {}).get("status") != "cached"
    assert cached3.metadata.get("cache", {}).get("status") != "cached"
    assert cached_user_only3.metadata.get("cache", {}).get("status") == "cached"

    # check paginate

    assert User.cache("id").join().paginate(limit=1, page=1).metadata["cache"].get("status") == "fresh"
    assert User.cache("id").join().paginate(limit=1, page=1).metadata["cache"].get("status") == "cached"

    data = User.cache().join().paginate(limit=1, page=2).metadata["cache"]
    assert data.get("status") == "fresh"
    assert not data.get("cached_at")
    assert User.cache().join().paginate(limit=1, page=2).metadata["cache"].get("status") == "cached"
    assert User.cache().join().paginate(limit=1, page=2).metadata["cache"].get("cached_at")

    remove_cache(1, "user")
    remove_cache([2], "user")

    assert User.cache("id").join().paginate(limit=1, page=1).metadata["cache"].get("status") == "fresh"
    assert User.cache().join().paginate(limit=1, page=2).metadata["cache"].get("status") == "fresh"

    # check chunk
    for chunk in User.cache().join().chunk(2):
        assert chunk.metadata["cache"]["status"] == "fresh"

    for chunk in User.cache().join().chunk(2):
        assert chunk.metadata["cache"]["status"] == "cached"

    clear_cache()

    assert User.cache(ttl=2).collect().metadata["cache"].get("status") == "fresh"
    assert User.cache(ttl=2).collect().metadata["cache"].get("status") == "cached"
    assert User.cache(ttl=2).collect().metadata["cache"].get("cached_at")

    assert _TypedalCache.count()
    assert _TypedalCacheDependency.count()

    time.sleep(3)  # for TTL
    data = User.cache(ttl=2).collect().metadata["cache"]
    assert data.get("status") == "fresh"
    assert not data.get("cached_at")

    assert _TypedalCache.count()
    assert _TypedalCacheDependency.count()

    time.sleep(3)  # for TTL

    assert clear_expired()
    assert not clear_expired()

    assert not _TypedalCache.count()
    assert not _TypedalCacheDependency.count()

    # test updating/deleting cached records:
    User.cache().collect()
    # should be cached:
    users = User.cache().collect()

    # .cache().collect() should have added cache entries:
    assert _TypedalCache.count()
    assert _TypedalCacheDependency.count()

    users.update(name="Redacted")

    # .update() should have deleted the cache entries:
    assert not _TypedalCache.count()
    assert not _TypedalCacheDependency.count()

    assert set(User.cache().collect().column("name")) == {"Redacted"} == set(User.collect().column("name"))

    users.delete()

    assert not User.count()


def test_caching_dependencies():
    first_one, first_two = CacheFirst.bulk_insert([{"name": "one"}, {"name": "two"}])

    second_one, second_two = NoCacheSecond.bulk_insert(
        [
            {"name": "een"},
            {"name": "twee"},
        ]
    )

    CacheTwoRelationships.insert(first=first_one, second=second_one)
    CacheTwoRelationships.insert(first=first_two, second=second_two)

    assert CacheTwoRelationships.join().cache().collect().metadata["cache"].get("status") == "fresh"
    assert CacheTwoRelationships.join().cache().collect().metadata["cache"].get("status") == "cached"

    # invalidates cache:
    first_one.update_record(name="one 2.0")

    assert CacheTwoRelationships.join().cache().collect().metadata["cache"].get("status") == "fresh"
    assert CacheTwoRelationships.join().cache().collect().metadata["cache"].get("status") == "cached"

    # does not invalidate cache:
    second_one.update_record(name="een 2.0")

    rows = CacheTwoRelationships.join().cache().collect()
    assert rows.metadata["cache"].get("status") == "cached"

    for row in rows:
        # new name should be loaded into cache:
        assert row.first.name != "one"
        # old name should still be in cache for this one
        assert row.second.name != "een 2.0"


def test_illegal():
    with pytest.raises(ValueError), pytest.warns(UserWarning):

        class HasRelationship:
            something = relationship("...", condition=lambda: 1, on=lambda: 2)


def test_join_with_select():
    _setup_data()

    builder = User.select(User.id, User.gid, Article.id, Article.gid).where(id=2).join("articles")
    user = builder.first_or_fail()

    assert user.id
    assert user.gid
    assert not user.name
    assert user.articles[0].id
    assert user.articles[0].gid
    assert not hasattr(user.articles[0], "title")

    for user in builder.paginate(limit=1, page=1):
        assert user.id
        assert user.gid
        assert not user.name
        assert user.articles[0].id
        assert user.articles[0].gid
        assert not hasattr(user.articles[0], "title")

    for user in builder.collect():
        assert user.id
        assert user.gid
        assert not user.name
        assert user.articles[0].id
        assert user.articles[0].gid
        assert not hasattr(user.articles[0], "title")


def test_count_with_join():
    _setup_data()

    # 0. count via select:
    row = User.select(User.id, User.gid, Article.id, Article.gid).where(id=4).join("articles").first_or_fail()
    assert len(row.articles) == 2

    assert User.where(id=4).join("articles").count(User.id) == 1
    assert User.where(id=4).join("articles").count(Article.id) == 2
