import contextlib
import time
import types
import typing
import warnings
from datetime import datetime
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
from src.typedal.serializers import as_json
from typedal import TypedRows

db = TypeDAL("sqlite:memory")


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
        lazy="warn",  # default behavior
    )

    tags_tolerate = relationship(
        list["Tag"],
        # lambda self, _: (Tagged.entity == self.gid) & (Tagged.tag == Tag.id)
        # doing an .on with and & inside can lead to a cross join,
        # for relationships with pivot tables a manual on query with aliases is prefered:
        on=lambda entity, tag: [
            tagged := Tagged.unique_alias(),
            tagged.on(tagged.entity == entity.gid),
            tag.on((tagged.tag == tag.id)),
        ],
        lazy="tolerate",  # load but with warning
        explicit=True,  # only load when requested
    )


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
        ],
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
        ],
    )

    # tags

    tag_draft, tag_published, tag_breaking, tag_trending, tag_offtopic = Tag.bulk_insert(
        [
            {"name": "draft"},
            {"name": "published"},
            {"name": "breaking-news"},
            {"name": "trending"},
            {"name": "off-topic"},
        ],
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
        ],
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
    all_articles = Article.join("author", "secondary_author", "final_editor", "tags").collect().as_dict()

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

    articles1 = (
        Article.where(title="Article 1").join("author", "secondary_author", "final_editor", "tags").first_or_fail()
    )

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

    role_writer = Role.where(Role.name == "writer").join("users", "tags").first_or_fail()

    assert len(role_writer.users) == 2

    author1 = User.where(id=4).join("articles").first()

    assert (
        len(author1.as_dict()["articles"]) == len(author1.__dict__["articles"]) == len(dict(author1)["articles"]) == 2
    )


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


@contextlib.contextmanager
def no_warnings():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        yield
        if caught:
            raise AssertionError(f"Unexpected warnings: {[w.message for w in caught]}")


def test_get_relationship_after_initial():
    _setup_data()

    article1 = Article.where(title="Article 1").first_or_fail()
    article2 = Article.where(title="Article 1").join(Article.tags).first_or_fail()

    with pytest.warns(RuntimeWarning):
        assert article1.tags == []

    with pytest.warns(RuntimeWarning):
        # tolerate includes warning
        tags_tolerate = article1.tags_tolerate
        assert tags_tolerate

    with no_warnings():
        # expect no warnings
        assert as_json.encode(tags_tolerate) == as_json.encode(article2.tags)
        assert tags_tolerate == article2.tags


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
        "users",
        method="inner",
        condition_and=lambda role, user: ~user.name.like("Reader%"),
    ).first()

    assert role_with_users.users
    assert role_with_users.users[0].name != "Reader 1"

    # left:
    role_with_users = Role.join(
        "users",
        method="left",
        condition_and=lambda role, user: ~user.name.like("Reader%"),
    ).first()

    assert role_with_users.users
    assert role_with_users.users[0].name != "Reader 1"


def test_caching():
    _setup_data()

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

    assert _TypedalCache.count() > 0
    assert _TypedalCacheDependency.count() > 0

    clear_cache()
    assert _TypedalCache.count() == 0
    assert _TypedalCacheDependency.count() == 0

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


def test_caching_insert_invalidation():
    _setup_data()

    # Get the writer user
    writer = User.where(name="Writer 1").collect_or_fail().first()

    # Cache articles filtered by author
    articles = Article.where(author=writer).cache().collect_or_fail()
    assert len(articles) == 1
    assert articles.metadata["cache"]["status"] == "fresh"

    # Get from cache
    articles_cached = Article.where(author=writer).cache().collect_or_fail()
    assert articles_cached.metadata["cache"]["status"] == "cached"

    # Insert new article matching the filter
    Article.insert(title="New Article by Writer", author=writer)

    # Query again with same filter
    # Cache should be invalidated because a new row matching the filter was inserted
    articles_after = Article.where(author=writer).cache().collect_or_fail()

    # This is the failing case: status will likely still be "cached" when it should be "fresh"
    assert len(articles_after) == 2
    assert articles_after.metadata["cache"]["status"] == "fresh"


def test_caching_dependencies():
    first_one, first_two = CacheFirst.bulk_insert([{"name": "one"}, {"name": "two"}])

    second_one, second_two = NoCacheSecond.bulk_insert(
        [
            {"name": "een"},
            {"name": "twee"},
        ],
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


def test_memoize_caches_and_invalidates():
    _setup_data()

    def expensive_func(data: TypedRows[User], field: str) -> set:
        return set(data.column(field))

    users = User.all()

    # First call - fresh
    result1, status = db.memoize(expensive_func, users, field="name")
    assert status == "fresh"
    assert "Reader 1" in result1

    users = User.all()

    # Second call - should be cached
    result2, status = db.memoize(expensive_func, users, field="name")
    assert status == "cached"
    assert result1 == result2

    # Update a user
    User.where(name="Reader 1").update(name="Reader Updated")

    users = User.all()

    # Third call - cache should be invalidated, result changed
    result3, status = db.memoize(expensive_func, users, field="name")
    assert status == "fresh"
    assert "Reader Updated" in result3
    assert "Reader 1" not in result3

    # lambda:

    with pytest.raises(ValueError):
        db.memoize(lambda x: x, users.first(), ttl=datetime.now())

    db.memoize(lambda x: x, users.first(), key="echo_lambda", ttl=datetime.now())


def test_memoize_nested_dependencies():
    """
    Test that memoize tracks dependencies from nested database lookups,
    not just the input rows.
    """
    _setup_data()

    # Create a function that does nested lookups
    def process_users(users: TypedRows[User]) -> dict:
        result = {}
        for user in users:
            # This lookup inside the function should also be tracked
            roles = Role.where(Role.id.belongs([r.id for r in user.roles])).collect()
            result[user.id] = len(roles)

        return result

    # Get initial data
    users = User.cache().collect()
    assert len(users) > 0

    # First memoize - should be fresh
    result1, status = db.memoize(process_users, users)
    assert status == "fresh"

    # Second call - should be cached
    result2, status = db.memoize(process_users, users)
    assert status == "cached"

    # Update a Role that was accessed inside process_users
    role = Role.first()
    role.update_record(name="modified_role")

    # Third call - should be fresh (invalidated by Role change)
    # but currently will still be "cached" because we only tracked User deps
    result3, status = db.memoize(process_users, users)
    assert status == "fresh"


def test_memoize_nested_dependencies2():
    _setup_data()

    def something_slow():
        # no input to isolate dependency tracking within the callback
        # includes role via join, so change in role should invalidate!
        # (which happens when using _determine_dependencies_auto)
        return list(User.join())

    bogus, status = db.memoize(something_slow)
    assert status == "fresh"

    # no change
    bogus, status = db.memoize(something_slow)
    assert status == "cached"

    # user change
    User.first().update_record(name="New Name :)")
    bogus, status = db.memoize(something_slow)
    assert status == "fresh"

    # role change
    Role.first().update_record(name="New Role Name :)")
    bogus, status = db.memoize(something_slow)
    assert status == "fresh"

    # no change
    bogus, status = db.memoize(something_slow)
    assert status == "cached"


def test_memoize_with_empty_table():
    """
    Test memoization when the table has no data yet.
    Ensures cache invalidation works correctly when data is added later.
    """
    # (no setup data needed since we'll truncate anyway)

    # Clean up - ensure table is empty
    for table in db.tables:
        db[table].truncate()

    db.commit()

    # Memoize a function with empty table
    def get_all_users() -> list[str]:
        users = User.join().select("user.name").execute()  # also tests execute instead of collect
        return [user[User.name] for user in users]

    # First call with empty table
    result1, status1 = db.memoize(get_all_users)
    assert status1 == "fresh"
    assert result1 == []

    # Second call - should be cached
    result2, status2 = db.memoize(get_all_users)
    assert status2 == "cached"
    assert result2 == []

    # Now insert data into the empty table
    role = Role.insert(name="admin")
    User.insert(name="First User", roles=[role], main_role=role, extra_roles=[])
    db.commit()

    # Third call - cache should be invalidated due to insert
    result3, status3 = db.memoize(get_all_users)
    assert status3 == "fresh", "Cache should be invalidated after insert into previously empty table"
    assert len(result3) == 1
    assert "First User" in result3

    # Fourth call - should be cached again
    result4, status4 = db.memoize(get_all_users)
    assert status4 == "cached"
    assert result3 == result4


def test_illegal():
    with pytest.raises(ValueError), pytest.warns(UserWarning):

        class HasRelationship:
            something = relationship("...", condition=lambda: 1, on=lambda: 2)

    with pytest.raises(ValueError), pytest.warns(UserWarning):
        Tag.join(Tag.articles, condition=lambda: 1, on=lambda: 2)


def test_join_relationship_custom_on():
    _setup_data()

    rows1 = Tag.join(
        Tag.articles,
        condition=lambda tag, article: (Tagged.tag == tag.id) & (article.gid == Tagged.entity) & (article.author == 3),
        method="inner",
    )

    rows2 = Tag.join(
        Tag.articles,
        on=lambda tag, article: [
            tagged := Tagged.unique_alias(),
            (tagged.tag == tag.id) & (article.gid == tagged.entity) & (article.author == 3),
        ],
        method="inner",
    )

    assert all([row.articles for row in rows1])
    assert all([row.articles for row in rows2])


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


def test_accessing_raw_data():
    _setup_data()

    user = User.where(id=4).join("articles").first()

    # <User({"id": 4, "name": "Untagged Author", "roles": [], "gid": "967da807-eb13-46dc-a0f3-d5c04751edf4", "main_role": 2, "extra_roles": []}) + ['articles']>
    assert user._row

    # one row per user+article combination like how postgres returns it:
    assert len(user._rows) == 2

    assert {row.user.id for row in user._rows} == {4}

    assert {row.articles.id for row in user._rows} == {1, 2}


def test_nested_relationships():
    _setup_data()

    # old:
    users = Role.where(name="reader").join("users").first().users
    # 2 queries
    old_besties = {
        user.name: user.bestie.name if user.bestie else "-"
        for user in User.where(User.id.belongs(u.id for u in users)).join("bestie").orderby(User.name)
    }

    # new:

    new_besties = {
        user.name: user.bestie.name if user.bestie else "-"
        for user in Role.where(name="reader").join("users.bestie", "users.articles").first().users  # 1 query
    }

    # check:

    assert old_besties == new_besties == {"Editor 1": "-", "Reader 1": "Reader's Bestie", "Writer 1": "-"}

    # more complex:
    role = Role.where(name="reader").join(
        "users.bestie",
        "users.articles.final_editor",
        "users.articles.secondary_author",
    )

    nested_article = role.first().users[2].articles[0]

    assert nested_article.title == "Article 2"

    assert nested_article.secondary_author
    assert not nested_article.final_editor

    # complex, inner:
    role_inner = Role.where(name="reader").join(
        "users.bestie",
        "users.articles.final_editor",
        "users.articles.secondary_author",
        method="inner",
    )

    # no final_editor -> inner join should fail:
    assert not role_inner.first()


class City(TypedTable):
    gid = TypedField(str, default=uuid4)
    name: str


class Office(TypedTable):
    gid = TypedField(str, default=uuid4)
    address: str
    city_id: City
    company: "Company"

    city_alternative = relationship(City, lambda office, city: office.city_id == city.id)


class Company(TypedTable):
    name: str

    offices = relationship(list[Office], lambda self, other: other.company == self.id)


def test_nested_join_with_shared_foreign_key():
    """
    Test that nested joins properly load relationships for all items,
    especially when multiple items share the same foreign key value.

    Bug: When joining nested data like company.offices[0].city works
    but company.offices[1].city is None even though both offices
    reference the same city.
    """
    db.define(City)
    db.define(Company)
    db.define(Office)

    # Create a city
    san_francisco = City.insert(name="San Francisco")

    # Create a company
    tech_corp = Company.insert(name="Tech Corp")

    # Create multiple offices in the same city for the same company
    office1 = Office.insert(address="123 Market St", city_id=san_francisco.id, company=tech_corp.id)
    office2 = Office.insert(address="456 Mission St", city_id=san_francisco.id, company=tech_corp.id)
    office3 = Office.insert(address="789 Howard St", city_id=san_francisco.id, company=tech_corp.id)

    db.commit()

    # 1: direct Reference (not a Relationship)

    # Query company with nested join to offices and their cities
    company = Company.where(id=tech_corp.id).join("offices.city_id").first_or_fail()

    # All offices should be loaded
    assert len(company.offices) == 3, f"Expected 3 offices, got {len(company.offices)}"

    # BUG TEST: All offices should have their city relationship loaded
    # Not just the first one
    for idx, office in enumerate(company.offices, 1):
        assert office.city_id is not None, f"Office {idx} ('{office.address}'): city relationship is None (BUG!)"
        assert isinstance(office.city_id, City), (
            f"Office {idx}: city ({office.city_id}) is not a City instance but a {type(office.city_id)}"
        )
        assert office.city_id.name == "San Francisco", (
            f"Office {idx}: expected 'San Francisco', got '{office.city_id.name}'"
        )
        assert office.city_id.id == san_francisco.id, f"Office {idx}: city id mismatch"

    # 2. alternative: city_alternative (Relationship)

    # Query company with nested join to offices and their cities
    company = Company.where(id=tech_corp.id).join("offices.city_alternative").first_or_fail()

    # All offices should be loaded
    assert len(company.offices) == 3, f"Expected 3 offices, got {len(company.offices)}"

    # BUG TEST: All offices should have their city relationship loaded
    # Not just the first one
    for idx, office in enumerate(company.offices, 1):
        assert office.city_alternative is not None, (
            f"Office {idx} ('{office.address}'): city relationship is None (BUG!)"
        )
        assert isinstance(office.city_alternative, City), (
            f"Office {idx}: city ({office.city_alternative}) is not a City instance but a {type(office.city_alternative)}"
        )
        assert office.city_alternative.name == "San Francisco", (
            f"Office {idx}: expected 'San Francisco', got '{office.city_alternative.name}'"
        )
        assert office.city_alternative.id == san_francisco.id, f"Office {idx}: city id mismatch"
