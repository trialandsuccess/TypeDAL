from src.typedal import TypeDAL, TypedTable, relationship

db = TypeDAL("sqlite:memory")


def test_example_relationships():
    class Author(TypedTable):
        name: str

        posts = relationship(list["Post"], condition=lambda self, other: self.id == other.author, join="left")

    class Post(TypedTable):
        title: str
        author: Author

    db.define(Author)
    db.define(Post)

    first = Author.insert(name="first")
    Author.insert(name="second")
    Post.insert(title="Post 1", author=first)
    Post.insert(title="Post 2", author=first)

    user1, user2 = Author.join().collect()

    assert user1
    assert user2

    assert user1.posts
    assert len(user1.posts) == 2

    assert not user2.posts
