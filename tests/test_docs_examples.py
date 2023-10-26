from src.typedal import TypeDAL, TypedTable, relationship


def test_example_relationships():
    db = TypeDAL("sqlite:memory")
    # db = TypeDAL("sqlite://debug.db")

    class ExampleRole(TypedTable):
        name: str  # e.g. writer, editor

        authors = relationship(
            list["ExampleAuthor"], condition=lambda role, author: author.roles.contains(role.id), join="left"
        )

    class ExampleAuthor(TypedTable):
        name: str
        roles: list[ExampleRole]
        posts = relationship(list["ExamplePost"], condition=lambda author, post: author.id == post.author, join="left")

    class ExamplePost(TypedTable):
        title: str
        author: ExampleAuthor

        tags = relationship(
            list["ExampleTag"],
            on=lambda post, tag: [
                ExampleTagged.on(ExampleTagged.post == post.id),
                tag.on(tag.id == ExampleTagged.tag),
            ],
        )

    class ExampleTag(TypedTable):
        name: str

        posts = relationship(
            list["ExamplePost"],
            on=lambda tag, posts: [
                ExampleTagged.on(ExampleTagged.tag == tag.id),
                posts.on(posts.id == ExampleTagged.post),
            ],
        )

    class ExampleTagged(TypedTable):
        tag: ExampleTag
        post: ExamplePost

    db.define(ExampleRole)
    db.define(ExampleAuthor)
    db.define(ExamplePost)
    db.define(ExampleTag)
    db.define(ExampleTagged)

    for table in db.tables:
        db[table].truncate()

    writer, editor = ExampleRole.bulk_insert([{"name": "writer"}, {"name": "editor"}])

    first = ExampleAuthor.insert(name="first", roles=[writer, editor])
    ExampleAuthor.insert(name="second", roles=[editor])
    post1 = ExamplePost.insert(title="ExamplePost 1", author=first)
    ExamplePost.insert(title="ExamplePost 2", author=first)

    tag1 = ExampleTag.insert(name="first-tag")
    tag2 = ExampleTag.insert(name="second-tag")
    tag3 = ExampleTag.insert(name="third-tag")

    ExampleTagged.bulk_insert(
        [
            {"tag": tag1, "post": post1},
            {"tag": tag2, "post": post1},
            {"tag": tag3, "post": post1},
        ]
    )

    db.commit()

    # from user to roles, posts

    user1, user2 = ExampleAuthor.join().collect(verbose=True)

    assert user1
    assert user2

    assert user1.posts
    assert len(user1.posts) == 2

    assert not user2.posts

    assert len(user1.roles) == 2
    assert len(user2.roles) == 1

    # from post to user
    builder = ExamplePost.join().where(id=1)

    post = builder.first()

    assert post
    assert post.author.name == "first"

    # from roles to user
    role = ExampleRole.join().where(id=2).first()

    assert len(role.authors) == 2

    # from post to tag
    post = ExamplePost.where(id=1).join("tags").first()

    assert post
    assert len(post.tags) == 3

    # from tag to post
    tag = ExampleTag.where(id=1).join("posts").first()

    assert tag
    assert len(tag.posts) == 1
