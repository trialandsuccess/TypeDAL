# 7. Mixins

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
class MyTable(TypedTable, SlugMixin, slug_field="title"): # optionally add `slug_suffix` here.
    title: str  # Assuming 'title' is a field in your table
    # Define other fields here

# Now, whenever you insert a record into MyTable, the 'slug' field will be automatically generated based on the 'title' field.
```

## Creating Custom Mixins

To create your own mixins for additional functionality, follow these steps:

1. Define a class that inherits from `Mixin`.
2. Add the fields and methods you want to include in your mixin.
3. Optionally, implement the `__on_define__` method to perform any initialization or setup when defining the model.
4. Use your custom mixin by inheriting from it along with other base classes in your table definitions.

Here's a basic example of how to create and use a custom mixin:

```python
from typedal import TypeDAL, TypedTable
from typedal.mixins import Mixin
from typedal.fields import UploadField
from py4web import URL
from yatl import IMG



class HasImageMixin(Mixin):
    """
    A custom mixin example.
    """

    # Define your mixin fields here
    image: UploadField(uploadfolder="/shared_uploads", autodelete=True, notnull=False)

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

# Now you can use CustomMixin in your table definitions along with other mixins or base classes.

class Article(TypedTable, HasImageMixin):
    title: str
    
# ... insert article row with image here ... #

Article(id=1).img() # -> <img src=... />

```

By using these mixins, you can enhance the functionality of your models in a modular and reusable manner, saving you
time and effort in your development process.
