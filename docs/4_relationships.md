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


authors_with_roles = Author.join('roles').collect()
posts_with_author = Post.join().collect()  # join can be called without arguments to join all relationships (in this case only 'author')
post_deep = Post.join("author.roles").collect()  # nested relationship, accessible via post.author.roles
```

In this example, the `Post` table contains a reference to the `Author` table. In that case, the `Author` `id` is stored
in the `Post`'s `author` column.
Furthermore, the `Author` table contains a `list:reference` to `list[Role]`. This means multiple `id`s from the `Role`
table can be stored in the `roles` column of `Author`.

For these two cases, a Relationship is set-up automatically, which means `.join()` can work with those.

### Alternative Join Syntax

You can pass the relationship object directly instead of its name as a string:

```python
posts_with_author = Post.join(Post.author).collect()
```

This works, but note that `Post.author` is typed as `Relationship[Author]` at the class level, while `row.author` is
typed as `Author` at the instance level. Some editors may complain about type mismatches when using this syntax (e.g.,
reporting that `list[Tag]` isn't a `Relationship`). If you encounter type checking issues, use the string syntax
instead.

## Other Relationships

To get the reverse relationship, you'll have to tell TypeDAL how the two tables relate to each other (since guessing is
complex and unreliable).

For example, to set up the reverse relationship from author to posts:

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

Here, `contains` is used since `Author.roles` is a `list:reference`.
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

In this example, `Relationship["Sidekick"]` is added as an extra type hint, since the reference to the table in
`relationship("Sidekick", ...)` is a string. This has to be passed as a string, since the Sidekick class is defined
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
generating a `CROSS JOIN` instead of a `LEFT JOIN`, which is bad for performance.
In this example, `Tag` is connected to `Post` and vice versa via the `Tagged` table.
It is recommended to use the tables received as arguments from the lambda (e.g. `tag.on` instead of `Tag.on` directly),
since these use aliases under the hood, which prevents conflicts when joining the same table multiple times.

## Lazy Loading and Explicit Relationships

### Lazy Policy

The `lazy` parameter on a relationship controls what happens when you access relationship data without explicitly
joining it first:

```python
@db.define()
class User(TypedTable):
    name: str
    posts = relationship(list["Post"], condition=lambda user, post: user.id == post.author, lazy="forbid")
```

Available policies:

- **`"forbid"`**: Raises an error. Prevents N+1 query problems by making them fail fast.
- **`"warn"`**: Returns an empty value (empty list or `None`) with a console warning.
- **`"ignore"`**: Returns an empty value silently.
- **`"tolerate"`**: Fetches the data but logs a warning about potential performance issues.
- **`"allow"`**: Fetches the data silently.

If `lazy=None` (the default), the relationship uses the database's default lazy policy. You can set this globally via
`TypeDAL`'s `lazy_policy` option (see [7. Advanced Configuration](./7_configuration.md) for configuration details),
which defaults to `"tolerate"`.

### Explicit Relationships

Use `explicit=True` for relationships that are expensive to join or rarely needed:

```python
@db.define()
class User(TypedTable):
    name: str
    audit_logs = relationship(list["AuditLog"], condition=lambda user, log: user.id == log.user, explicit=True)
```

When you call `.join()` without arguments, explicit relationships are skipped:

```python
user = User.join().first()  # user.audit_logs follows the lazy policy (empty/error/warning depending on setting)
```

To include an explicit relationship, reference it by name:

```python
user = User.join("audit_logs").first()  # now user.audit_logs is populated
```

## What's Next?

Depending on your setup:

- **Using py4web or web2py?** → [5. py4web & web2py](./5_py4web.md)
- **Ready to manage your database?** → [6. Migrations](./6_migrations.md)
- **Dive deeper into functionality?** → [8.: Mixins](./8_mixins.md)

