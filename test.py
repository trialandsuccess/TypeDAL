import typing
from sqlite3 import IntegrityError

from typedal import *
import pydal

from typedal.fields import TextField, ReferenceField

db = TypeDAL("sqlite:memory")

### DEFINE

# before:

db.define_table("relation")

db.define_table("old_syntax",
                pydal.Field("name", "string", notnull=True),
                pydal.Field("age", "float", notnull=False),
                pydal.Field("location", "text", default="Amsterdam"),
                pydal.Field("relation", "reference relation")
                )


# after:

@db.define
class NewRelation(TypedTable):
    ...


class SecondNewRelation(TypedTable):
    ...


# db.define can be used as decorator or later on
db.define(SecondNewRelation)


# you can use native types or TypedField (if more settings are required, otherwise default are used)

@db.define
class FirstNewSyntax(TypedTable):
    # simple:
    name: str
    # optional: (sets required=False and notnull=False)
    age: float | None
    # with extra options (and non-native type 'text'):
    location: TypedField(str, type="text", default="Amsterdam")
    # references:
    # can only be made optional with typing.Optional, not '| None'
    first_new_relation: typing.Optional[NewRelation]
    second_new_relation: typing.Optional[SecondNewRelation]
    # backwards compatible:
    old_relation: typing.Optional[db.relation]


# instead of using just a native type, TypedField can also always be used:
class SecondNewSyntax(TypedTable):
    # simple:
    name: TypedField(str)
    # optional: (sets required=False and notnull=False)
    # note: TypedField can NOT be used with typing.Optional or '| None' !!
    age: TypedField(float, notnull=False)
    # with extra options (and non-native type 'text'):
    location: TextField(default="Rotterdam")
    first_new_relation: ReferenceField(NewRelation)
    second_new_relation: ReferenceField(db.second_new_relation)
    # backwards compatible:
    old_relation: TypedField(db.relation, notnull=False)

db.define(SecondNewSyntax)

### INSERTS
db.relation.insert()

db.new_relation.insert()
# OR
NewRelation.insert()
SecondNewRelation.insert()

## insert without all required:

try:
    db.old_syntax.insert()
    raise ValueError("RuntimeError should be raised (required)")
except IntegrityError:
    # Table: missing required field: name
    ...

try:
    db.first_new_syntax.insert()
except IntegrityError:
    # Table: missing required field: name
    ...

# equals:

try:
    FirstNewSyntax.insert()
except IntegrityError:
    # Table: missing required field: name
    ...

try:
    SecondNewSyntax.insert()
    raise ValueError("RuntimeError should be raised (required)")
except IntegrityError:
    # Table: missing required field: name
    ...

## insert normal
db.old_syntax.insert(name="First", age=99, location="Norway", relation=db.relation(id=1))
db.first_new_syntax.insert(name="First", age=99, location="Norway", old_relation=db.relation(id=1))
# equals
FirstNewSyntax.insert(name="First", age=99, location="Norway", old_relation=db.relation(id=1))
# similar
SecondNewSyntax.insert(name="Second", age=101, first_new_relation=NewRelation(id=1),
                       second_new_relation=SecondNewRelation(id=1))

### Select
from pprint import pprint


def _print_and_assert_len(lst, exp):
    pprint(lst)
    real = len(lst)
    assert real == exp, f"{real} != {exp}"


_print_and_assert_len(db(db.old_syntax).select().as_list(), 1)
_print_and_assert_len(db(db.old_syntax.id > 0).select().as_list(), 1)

_print_and_assert_len(db(db.first_new_syntax).select().as_list(), 2)
_print_and_assert_len(db(db.first_new_syntax.id > 0).select().as_list(), 2)

_print_and_assert_len(db(FirstNewSyntax).select().as_list(), 2)
_print_and_assert_len(db(FirstNewSyntax.id > 0).select().as_list(), 2)

_print_and_assert_len(db(SecondNewSyntax).select().as_list(), 1)
_print_and_assert_len(db(SecondNewSyntax.id > 0).select().as_list(), 1)
