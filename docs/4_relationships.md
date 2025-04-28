# 4. Relationships

## Reference Fields

```python
@db.define()
class Role(TypedTable):
    name: str  # e.g. writer, editor


@db.define()
class Author(TypedTable):
    name: str
    roles: list[Role]


@db.define()
class Post(TypedTable):
    title: str
    author: Author


authors_with_roles = Author.join('role').collect()
posts_with_author = Post.join().collect()  # join can be called without arguments to join all relationships (in this case only 'author')
```

In this example, the `Post` table contains a reference to the `Author` table. In that case, the `Author` `id` is stored
in the
`Post`'s `author` column.
Furthermore, the `Author` table contains a `list:reference` to `list[Role]`. This means multiple `id`s from the `Role`
table
can be stored in the `roles` column of `Author`.

For these two cases, a Relationship is set-up automatically, which means `.join()` can work with those.

## Other Relationships

To get the reverse relationship, you'll have to tell TypeDAL how the two tables relate to each other (since guessing is
complex and unreliable).

For example, to set up the reverse relationshp from author to posts:

```python
@db.define()
class Author(TypedTable):
    name: str

    posts = relationship(list["Post"], condition=lambda author, post: author.id == post.author, join="left")

```

Note that `"Post"` is in quotes. This is because the `Post` class is defined later, so a reference to it is not
available yet.

And to set up the relationship from `Roles` to `Author`:

```python
@db.define()
class Role(TypedTable):
    name: str  # e.g. writer, editor

    authors = relationship(list["Author"], condition=lambda role, author: author.roles.contains(role.id), join="left")
```

Here, contains is used since `Author.roles` is a `list:reference`.
See [the web2py docs](http://www.web2py.com/books/default/chapter/29/06/the-database-abstraction-layer#list-type-and-contains)
for more details.

## One-to-One

```python
# assuming every superhero has exactly one side kick, and a sidekick 'belongs to' one superhero:
@db.define()
class SuperHero(TypedTable):
    name: str
    sidekick: Relationship["Sidekick"] = relationship("Sidekick", lambda hero, sidekick: hero.id == sidekick.superhero)


@db.define()
class Sidekick(TypedTable):
    name: str
    superhero: SuperHero
```

In this example, `Relationship["Sidekick"]` is added as an extra type hint, since the reference to the table
in `relationship("Sidekick", ...)` is a string. This has to be passed as a string, since the Sidekick class is defined
after the superhero class.
Adding the `Relationship["Sidekick"]` hint is optional, but recommended to improve editor support.

## Many-to-Many

Setting up a relationship that uses a junction/pivot table is slightly harder.

```python

# with `unique_alias()` which is better if you have multiple joins:

@db.define()
class Post(TypedTable):
    title: str
    author: Author

    tags = relationship(list["Tag"], on=lambda post, tag: [
        # post and tag already have a unique alias, create one for tagged here:
        tagged := Tagged.unique_alias(),
        tagged.on(tagged.post == post.id),
        tag.on(tag.id == tagged.tag),
    ])

# without unique alias:

@db.define()
class Tag(TypedTable):
    name: str

    posts = relationship(list["Post"], on=lambda tag, posts: [
        Tagged.on(Tagged.tag == tag.id),
        posts.on(posts.id == Tagged.post),
    ])


@db.define()
class Tagged(TypedTable):
    tag: Tag
    post: Post
```

Instead of a `condition`, it is recommended to define an `on`. Using a condition is possible, but could lead to pydal
generation a `CROSS JOIN` instead of a `LEFT JOIN`, which is bad for performance.
In this example, `Tag` is connected to `Post` and vice versa via the `Tagged` table.
It is recommended to use the tables received as arguments from the lambda (e.g. `tag.on` instead of `Tag.on` directly),
since these use aliases under the hood, which prevents conflicts when joining the same table multiple times.
