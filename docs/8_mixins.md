# 8. Mixins

Mixins allow you to encapsulate reusable fields and behaviors that can be easily
added to your database models. On this page, we'll walk through the usage
of two example mixins provided by TypeDAL: `TimestampsMixin` for automatic timestamping and `SlugMixin` for generating
URL-friendly slugs. Additionally, we'll demonstrate how you can create your own custom mixins to tailor functionality to
your app's specific needs.

## Using `TimestampsMixin`

The `TimestampsMixin` adds automatic timestamping functionality to your models. It includes two fields: `created_at`
and `updated_at`, which record the creation and last update times respectively.

Here's how you can use it:

```python
from typedal import TypeDAL, TypedTable
from typedal.mixins import TimestampsMixin


# Define your table with TimestampsMixin
class MyTable(TypedTable, TimestampsMixin):
    # Define your table fields here
    pass

# Now, whenever you create or update a record in MyTable, the 'created_at' and 'updated_at' timestamps will be automatically managed.
```

## Using `SlugMixin`

The `SlugMixin` adds a "slug" field to your models, which is a URL-friendly version of another field's value. This is
typically used for SEO-friendly URLs. To prevent duplicates, some random bytes are appended at the end of the slug.
You can control the amount of bytes added via the `slug_suffix` option (`slug_suffix=0` to disable the behavior).

Here's how you can use it:

```python
from typedal import TypeDAL, TypedTable
from typedal.mixins import SlugMixin


# Define your table with SlugMixin, specifying the field to base the slug on
class MyTable(TypedTable, SlugMixin, slug_field="title"):
    title: str  # Assuming 'title' is a field in your table
    # Define other fields here

# Now, whenever you insert a record into MyTable, the 'slug' field will be automatically generated based on the 'title' field.
```

## Using `PydanticMixin`

The `PydanticMixin` enables seamless integration with Pydantic-based frameworks (like FastAPI) by adding schema generation
capabilities to your models. Without this mixin, you cannot return TypedTable instances directly as FastAPI responses
or use them with `pydantic.TypeAdapter`.

Add the mixin to enable `model_dump()` for serialization, including support for relationships and computed properties:

```python
from typedal import TypedField, TypedTable
from typedal.mixins import PydanticMixin


class Author(TypedTable, PydanticMixin):
    name: str


class Book(TypedTable, PydanticMixin):
    title: str
    author: Author
    private_notes = TypedField(str, readable=False)

    @property
    def display_title(self) -> str:
        return f"{self.title} by {self.author.name}"


# After inserting records and joining relationships:
book = Book.where(id=1).join("author").first()

# model_dump() serializes the full object graph
data = book.model_dump()
# -> {"id": 1, "title": "...", "author": {"id": 1, "name": "..."}, "display_title": "..."}
# (private_notes is omitted because readable=False)

# Runtime readability flags are also respected by model_dump()
Book.title.readable = False
data = book.model_dump()
# -> {"id": 1, "author": {"id": 1, "name": "..."}, "display_title": "..."}

# Use mode="json" for JSON-serializable output (dates as ISO strings, etc.)
data = book.model_dump(mode="json")
```

> **Note:** When referencing other TypedTable models from a PydanticMixin class, those models must also include
> `PydanticMixin`. This ensures the entire object graph can be serialized consistently.

With this mixin, TypedTable instances work seamlessly as FastAPI response models:

```python
from fastapi import FastAPI

app = FastAPI()

@app.get("/books/{book_id}")
def get_book(book_id: int) -> Book:
    return Book.where(id=book_id).join("author").first()
```

## Creating Custom Mixins

To create your own mixins for additional functionality, follow these steps:

1. Define a class that inherits from `Mixin`.
2. Add the fields and methods you want to include in your mixin.
3. Optionally, implement the `__on_define__` method to perform any initialization or setup when defining the model.
4. Use your custom mixin by inheriting from it along with other base classes in your table definitions.

Here's a basic example of how to create and use a custom mixin:

```python
import datetime as dt
import typing as t

from typedal import TypeDAL, TypedTable, QueryBuilder
from typedal.mixins import Mixin, TimestampsMixin
from typedal.fields import UploadField
from py4web import URL
from yatl import IMG


class HasImageMixin(Mixin):
    """
    A custom mixin example.
    """

    # Define your mixin fields here
    image = UploadField(uploadfolder="/shared_uploads", autodelete=True, notnull=False)

    def img(self, **options) -> IMG:
        """
        Custom method.
        """

        return IMG(_src=URL("download", self.image), **options)

    @classmethod
    def __on_define__(cls, db: TypeDAL) -> None:
        """
        Custom initialization method - optional.
        """
        super().__on_define__(db)
        # Add any custom initialization logic here


# Now you can use HasImageMixin in your table definitions along with other mixins or base classes.

class Article(TypedTable, TimestampsMixin, HasImageMixin):
    title: str
    
    # this could also be a class method of Timestamps Mixin:
    @classmethod
    def recently_updated(cls, hours: int = 24) -> QueryBuilder[t.Self]:
        """Return records updated in the last N hours."""
        cutoff = dt.datetime.now() - dt.timedelta(hours=hours)
        return QueryBuilder(cls).where(cls.updated_at >= cutoff)

# Retrieve a record and use the custom method
article = Article(id=1)
article.img()  # -> <img src=... />

# Use the classmethod to get recently updated articles
recent_articles = (
    Article.recently_updated(hours=12)
    .where(published=True)
    .collect()
)
```

> **Note:** The `img()` example uses py4web utilities (URL, IMG), but the mixin itself works identically in any setup.

By using these mixins, you can enhance the functionality of your models in a modular and reusable manner, saving you
time and effort in your development process.


---

Looking to cache expensive function results? Head to [9. Function Memoization](./9_memoization.md) to learn about `db.memoize()`.
