"""
Joined relationships live under an alias; where() and orderby() on the joined table resolve to it when the builder runs.
"""

import pytest

from src.typedal import QueryBuilder, TypeDAL, TypedTable, relationship
from src.typedal.exceptions import AliasedTableMismatchError


def define_models(db: TypeDAL):
    @db.define()
    class AliasAuthor(TypedTable):
        name: str

        articles = relationship(
            list["AliasArticle"],
            condition=lambda author, article: author.id == article.author,
            join="left",
        )

    @db.define()
    class AliasArticle(TypedTable):
        title: str
        author: AliasAuthor
        reviewer: AliasAuthor

        writer = relationship(AliasAuthor, condition=lambda article, author: article.author == author.id, join="left")
        critic = relationship(AliasAuthor, condition=lambda article, author: article.reviewer == author.id, join="left")
        comments = relationship(
            list["AliasComment"],
            condition=lambda article, comment: article.id == comment.article,
            join="left",
        )
        # `on` joins the table itself, without an alias:
        replies = relationship(
            list["AliasComment"],
            on=lambda article, comment: [comment.on(comment.article == article.id)],
        )

    @db.define()
    class AliasComment(TypedTable):
        article: AliasArticle
        body: str

        post = relationship(AliasArticle, condition=lambda comment, article: comment.article == article.id)

    ann, bob, cat = AliasAuthor.bulk_insert([{"name": "ann"}, {"name": "bob"}, {"name": "cat"}])
    first = AliasArticle.insert(title="First", author=ann, reviewer=bob)
    AliasArticle.insert(title="Second", author=bob, reviewer=ann)
    third = AliasArticle.insert(title="Third", author=ann, reviewer=cat)
    AliasComment.bulk_insert(
        [
            {"article": first, "body": "nice"},
            {"article": first, "body": "meh"},
            {"article": third, "body": "nice"},
        ]
    )
    db.commit()

    return AliasAuthor, AliasArticle, AliasComment


db = TypeDAL("sqlite:memory")
Author, Article, Comment = define_models(db)


def titles(builder) -> list[str]:
    return [row.title for row in builder]


def test_filter_on_left_join_paginates_and_counts():
    builder = Article.join("comments").where(Comment.body == "nice").orderby(Article.id)

    rows = builder.collect()
    assert titles(rows) == ["First", "Third"]
    # the filter applies to the joined rows too, like condition_and does:
    assert [[comment.body for comment in row.comments] for row in rows] == [["nice"], ["nice"]]

    # with limitby, the predicate moves into an id subquery, which needs the left join as well:
    for page_number, expected_title in enumerate(["First", "Third"], start=1):
        page = builder.paginate(limit=1, page=page_number)
        assert titles(page) == [expected_title]
        assert (page.pagination["total_items"], page.pagination["total_pages"]) == (2, 2)
        assert [comment.body for comment in page.first().comments] == ["nice"]

    assert builder.count() == 2
    assert builder.count(distinct=Article.id) == 2
    assert "LEFT JOIN" in builder._count()

    with_inner = Article.join("writer", method="inner").join("comments").where(Comment.body == "nice")
    assert with_inner.count() == 2
    assert with_inner.paginate(limit=1).pagination["total_items"] == 2


def test_count_distinct_joined_field():
    # "Second" has no comments, so its left-joined row is NULL, which isn't a distinct value:
    assert Article.join("comments").count(distinct=Comment.body) == 2
    assert Article.join("comments").where(Article.title != "Third").count(distinct=Comment.body) == 2


def test_required_left_joins_ignores_expressions_without_table_targets():
    """Ignore non-table targets while retaining transitive join dependencies."""
    writer_join = Author.on(Author.id == Article.author)
    article_join = Article.on(Article.author == Author.id)
    comment_join = Comment.on(Comment.article == Article.id)
    non_table_expression = Author.name == "ann"

    assert QueryBuilder._required_left_joins(
        {Comment._tablename},
        [non_table_expression, writer_join, article_join, comment_join],
    ) == [writer_join, article_join, comment_join]
    assert QueryBuilder._required_left_joins(set(), [non_table_expression, comment_join]) == []


def test_count_distinct_roots_applies_left_join_condition_and():
    """Distinct root counts include only comments matching the additional join condition."""
    builder = Article.join("comments", condition_and=lambda _article, comment: comment.body == "meh")

    rows = builder.orderby(Article.id).collect()
    assert titles(rows) == ["First", "Second", "Third"]
    assert [[comment.body for comment in row.comments] for row in rows] == [["meh"], [], []]
    assert builder.count(distinct=True) == 1


@pytest.mark.parametrize("method", ["left", "inner"])
def test_orderby_on_joined_table_resolves(method):
    builder = Article.join("writer", method=method).orderby(~Author.name, Article.id)

    assert titles(builder) == ["Second", "First", "Third"]
    assert [titles(builder.paginate(limit=1, page=page)) for page in range(1, 4)] == [["Second"], ["First"], ["Third"]]


@pytest.mark.parametrize("method", ["left", "inner"])
def test_nested_relationship_resolves(method):
    builder = Author.join("articles.comments", method=method).where(Comment.body == "meh")

    rows = builder.collect()
    assert [row.name for row in rows] == ["ann"]
    assert [article.title for article in rows.first().articles] == ["First"]
    assert [comment.body for comment in rows.first().articles[0].comments] == ["meh"]
    assert builder.count() == 1
    page = builder.paginate(limit=1)
    assert [row.name for row in page] == ["ann"]
    assert (page.pagination["total_items"], page.pagination["total_pages"]) == (1, 1)

    by_path = Author.join("articles.comments", method=method).where(
        lambda author, articles__comments: articles__comments.body == "meh"
    )
    by_name = Author.join("articles.comments", method=method).where(lambda author, comments: comments.body == "meh")
    assert [row.name for row in by_path] == [row.name for row in by_name] == ["ann"]


def test_custom_on_intermediate_join_filter_counts_and_paginates():
    db = TypeDAL("sqlite:memory")

    try:
        @db.define()
        class Parent(TypedTable):
            name: str

            children = relationship(
                list["Child"],
                on=lambda parent, child: [
                    ParentChild.on(ParentChild.parent == parent.id),
                    child.on(child.id == ParentChild.child),
                ],
                join="left",
            )

        @db.define()
        class Child(TypedTable):
            name: str

        @db.define()
        class ParentChild(TypedTable):
            parent: Parent
            child: Child

        Parent.insert(name="unlinked")
        linked = Parent.insert(name="linked")
        child = Child.insert(name="matching")
        ParentChild.insert(parent=linked, child=child)

        builder = Parent.join("children").where(Child.name == "matching").orderby(Parent.id)
        rows = builder.collect()
        assert [row.id for row in rows] == [linked.id]
        assert [child.id for child in rows.first().children] == [child.id]

        assert builder.count() == 1
        assert builder.count(distinct=Parent.id) == 1
        page = builder.paginate(limit=1)
        assert [row.id for row in page] == [linked.id]
        assert (page.pagination["total_items"], page.pagination["total_pages"]) == (1, 1)
    finally:
        db.close()


def test_paginated_join_deduplicates_root_ids_before_limit():
    builder = Article.join("comments").where(Comment.id > 0).orderby(Comment.id)

    assert titles(builder.collect()) == ["First", "Third"]
    assert builder.count(distinct=Article.id) == 2

    first_page = builder.paginate(limit=1)
    second_page = builder.paginate(limit=1, page=2)

    assert (first_page.pagination["total_items"], first_page.pagination["total_pages"]) == (2, 2)
    assert titles(first_page) == ["First"]
    assert titles(second_page) == ["Third"]


def test_filter_for_missing_left_join_rows():
    builder = Article.join("comments").where(Comment.id == None)

    assert titles(builder) == ["Second"]
    assert builder.paginate(limit=1).pagination["total_items"] == 1
    assert builder.count() == 1


def test_expression_on_joined_table_resolves():
    builder = Article.join("comments", method="inner").where(Comment.body.upper() == "MEH")

    assert titles(builder) == ["First"]
    assert builder.count() == 1


def test_same_table_joined_twice_needs_a_lambda():
    builder = Article.join("writer").join("critic")

    with pytest.raises(AliasedTableMismatchError, match="multiple aliases.*'writer', 'critic'"):
        builder.where(Author.name == "ann").to_sql()

    assert titles(builder.where(lambda article, critic: critic.name == "ann")) == ["Second"]
    assert titles(builder.where(lambda article, writer: writer.name == "ann").orderby(Article.id)) == [
        "First",
        "Third",
    ]
    # the lambda is resolved when the builder runs, so it can come before the joins:
    deferred = Article.where(lambda article, critic: critic.name == "ann").join("writer").join("critic")
    assert titles(deferred) == ["Second"]
    assert deferred.count() == 1
    assert deferred.paginate(limit=1).pagination["total_items"] == 1


def test_lambda_mixes_with_other_where_parts():
    builder = Article.join("critic").where(
        lambda article, critic: critic.name == "cat",
        lambda article: article.title == "First",
        title="Second",
    )

    assert titles(builder.orderby(Article.id)) == ["First", "Second", "Third"]

    # like a plain lambda, one that returns nothing doesn't filter:
    assert len(Article.join("critic").where(lambda article, critic: None).collect()) == 3

    # an argument with a default doesn't ask for a joined table:
    assert titles(Article.where(lambda article, title="Second": article.title == title)) == ["Second"]


def test_lambda_asking_for_unjoined_relationship():
    with pytest.raises(ValueError, match="unjoined relationship.*critic.*joined: none"):
        Article.where(lambda article, critic: critic.name == "ann").to_sql()

    with pytest.raises(ValueError, match=r"unjoined relationship\(s\) critic \(joined: writer\)"):
        Article.join("writer").where(lambda article, critic: critic.name == "ann").collect()


def test_nested_one_to_many_collects_every_row():
    # "First" has two comments, which arrive on two rows that repeat ann and "First":
    authors = Author.join("articles.comments").orderby(Author.id).collect()

    assert [
        (author.name, [(article.title, sorted(c.body for c in article.comments)) for article in author.articles])
        for author in authors
    ] == [
        ("ann", [("First", ["meh", "nice"]), ("Third", ["nice"])]),
        ("bob", [("Second", [])]),
        ("cat", []),
    ]


def test_nested_relationship_back_to_the_root_table():
    def overview(builder):
        return [(row.title, row.writer.name, sorted(a.title for a in row.writer.articles)) for row in builder]

    expected = [
        ("First", "ann", ["First", "Third"]),
        ("Second", "bob", ["Second"]),
        ("Third", "ann", ["First", "Third"]),
    ]

    assert overview(Article.join("writer.articles").orderby(Article.id)) == expected
    assert overview(Article.join("writer.articles", method="inner").orderby(Article.id)) == expected

    # a selected root field stays the root's, it isn't moved to the nested 'articles' alias:
    selected = Article.join("writer.articles").select(Article.title).orderby(Article.id).collect()
    assert [row.title for row in selected] == ["First", "Second", "Third"]
    assert [len(row.writer.articles) for row in selected] == [2, 1, 2]

    # and a filter on the root table keeps meaning the root:
    assert overview(Article.join("writer.articles").where(Article.title == "Second")) == [("Second", "bob", ["Second"])]
    assert (
        Article.join("writer.articles").where(Article.title == "Second").paginate(limit=1).pagination["total_items"]
        == 1
    )


def test_ambiguous_nested_name_needs_full_path():
    builder = Comment.join("post.writer.articles", "post.critic.articles")

    with pytest.raises(ValueError, match="unjoined relationship.*articles"):
        builder.where(lambda comment, articles: articles.title == "Second").to_sql()

    # comments on posts whose writer (ann) also wrote "Third":
    by_writer = builder.where(lambda comment, post__writer__articles: post__writer__articles.title == "Third")
    assert sorted(row.body for row in by_writer) == ["meh", "nice", "nice"]
    # comments on posts whose critic (bob, cat) wrote "Second": only bob reviewed one with comments
    by_critic = builder.where(lambda comment, post__critic__articles: post__critic__articles.title == "Second")
    assert sorted(row.body for row in by_critic) == ["meh", "nice"]


def test_on_relationship_keeps_the_table_itself():
    replies = Article.join("replies").where(Comment.body == "meh")

    assert titles(replies) == ["First"]
    assert titles(replies.paginate(limit=1)) == ["First"]
    assert replies.count() == 1

    # joined both with and without an alias: the table name means the un-aliased `on` join
    both = Article.join("replies", "comments").where(Comment.body == "meh")
    assert titles(both) == ["First"]
    assert sorted(comment.body for comment in both.first().comments) == ["meh", "nice"]


def test_mutations_reject_lambdas_on_joined_tables():
    builder = Article.join("critic").where(lambda article, critic: critic.name == "ann")

    for mutation in (builder.delete, builder._delete):
        with pytest.raises(ValueError, match=r"delete\(\) and update\(\)"):
            mutation()
    for mutation in (builder.update, builder._update):
        with pytest.raises(ValueError, match=r"delete\(\) and update\(\)"):
            mutation(title="changed")

    assert Article.count() == 3


def test_cache_key_does_not_depend_on_alias_hashes():
    # (compared directly: these models are local classes, which caching can't pickle)
    def build():
        # every join() clones the relationship, so each builder gets its own alias hashes;
        # 'replies' is joined without an alias, so Comment keeps its table name
        return Article.join("replies", "critic").where(lambda article, critic: critic.name == "bob").where(
            Comment.body == "meh"
        )

    first, second = build(), build()
    assert hash(first.relationships["critic"]) != hash(second.relationships["critic"])
    key = str(first._cache_key_query())
    assert key == second._cache_key_query()
    assert "<critic>" in key
    assert '"alias_comment"."body"' in key


def test_left_join_filter_paginates_with_distinct_psql(dal_psql: TypeDAL):
    _, article, comment = define_models(dal_psql)

    builder = article.join("comments").where(comment.body == "nice")
    assert titles(builder.paginate(limit=1)) == ["First"]
    assert builder.paginate(limit=1).pagination["total_items"] == 2

    ordered = builder.orderby(~comment.id)
    assert titles(ordered.paginate(limit=1)) == ["Third"]
    assert ordered.count(distinct=article.id) == 2
