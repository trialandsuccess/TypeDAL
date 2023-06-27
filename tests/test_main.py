import re
import typing
from sqlite3 import IntegrityError

import pydal
import pytest

from src.typedal import *
from src.typedal.__about__ import __version__
from src.typedal.fields import *


def test_about():
    version_re = re.compile(r"\d+\.\d+\.\d+.*")
    assert version_re.findall(__version__)


db = TypeDAL("sqlite:memory")


def test_mixed_defines():
    ### DEFINE

    # before:

    db.define_table("relation")

    db.define_table(
        "old_syntax",
        pydal.Field("name", "string", notnull=True),
        pydal.Field("age", "float", notnull=False),
        pydal.Field("location", "text", default="Amsterdam"),
        pydal.Field("relation", "reference relation"),
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
        location = TypedField(str, type="text", default="Amsterdam")
        # references:
        # can only be made optional with typing.Optional, not '| None'
        first_new_relation: typing.Optional[NewRelation]
        second_new_relation: typing.Optional[SecondNewRelation]
        # backwards compatible:
        old_relation: typing.Optional[db.relation]
        # generics:
        tags: list[str]

    # instead of using just a native type, TypedField can also always be used:
    class SecondNewSyntax(TypedTable):
        # simple:
        name = TypedField(str)
        # optional: (sets required=False and notnull=False)
        # note: TypedField can NOT be used with typing.Optional or '| None' !!
        age = TypedField(float, notnull=False)
        # with extra options (and non-native type 'text'):
        location = TextField(default="Rotterdam")
        first_new_relation = ReferenceField(NewRelation)
        second_new_relation = ReferenceField(db.second_new_relation)
        # backwards compatible:
        old_relation = TypedField(db.relation, notnull=False)
        # generics:
        tags = TypedField(list[str])

    db.define(SecondNewSyntax)

    ### INSERTS
    db.relation.insert()

    db.new_relation.insert()
    # OR
    NewRelation.insert()
    SecondNewRelation.insert()

    ## insert without all required:

    with pytest.raises(IntegrityError):
        db.old_syntax.insert()

    with pytest.raises(IntegrityError):
        db.first_new_syntax.insert()

    # equals:

    with pytest.raises(IntegrityError):
        FirstNewSyntax.insert()

    with pytest.raises(IntegrityError):
        SecondNewSyntax.insert()

    ## insert normal
    db.old_syntax.insert(name="First", age=99, location="Norway", relation=db.relation(id=1))
    db.first_new_syntax.insert(
        name="First", age=99, location="Norway", old_relation=db.relation(id=1), tags=["first", "second"]
    )
    # equals
    FirstNewSyntax.insert(
        name="First", age=99, location="Norway", old_relation=db.relation(id=1), tags=["first", "second"]
    )
    # similar
    SecondNewSyntax.insert(
        name="Second",
        age=101,
        first_new_relation=NewRelation(id=1),
        second_new_relation=SecondNewRelation(id=1),
        tags=["first", "second"],
    )

    ### Select
    from pprint import pprint

    assert db(FirstNewSyntax.name == "First").count()
    assert db(FirstNewSyntax.location == "Norway").count()
    assert not db(FirstNewSyntax.location == "Nope").count()
    assert not db(FirstNewSyntax.location == "Nope").count()

    assert db(SecondNewSyntax.name == "Second").count()
    assert db(SecondNewSyntax.location == "Rotterdam").count()
    assert not db(SecondNewSyntax.location == "Nope").count()
    assert not db(SecondNewSyntax.location == "Nope").count()

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

    assert SecondNewSyntax(id=1) is not None
    assert SecondNewSyntax(1) is not None
    assert SecondNewSyntax(id=2) is None
    assert SecondNewSyntax(2) is None
    _print_and_assert_len(db(SecondNewSyntax).select().as_list(), 1)
    _print_and_assert_len(db(SecondNewSyntax.id > 0).select().as_list(), 1)

    assert SecondNewSyntax(1).location == "Rotterdam"


def test_dont_allow_bool_in_query():
    with pytest.raises(ValueError):
        db(True)


def test_invalid_union():
    with pytest.raises(NotImplementedError):
        @db.define
        class Invalid(TypedTable):
            valid: int | None
            invalid: int | str

    with pytest.raises(NotImplementedError):
        @db.define
        class Invalid(TypedTable):
            valid: list[int]
            invalid: dict[str, int]


def test_using_model_without_define():
    class Invalid(TypedTable):
        name: str

    # no db.define used
    with pytest.raises(EnvironmentError):
        Invalid.insert(name="error")

    with pytest.raises(EnvironmentError):
        Invalid(name="error")


def test_typedfield_reprs():
    # str() and repr()

    assert str(TypedField(str)) == "TypedField.str"
    assert str(TypedField(str | None)) == "TypedField.str"
    assert str(TypedField(typing.Optional[str])) == "TypedField.str"
    assert repr(TypedField(str)) == "<TypedField.str with options {}>"
    assert str(TypedField(str, type="text")) == "TypedField.text"
    assert repr(TypedField(str, type="text", default="def")) == "<TypedField.text with options {'default': 'def'}>"
    assert str(TextField()) == "TypedField.text"
    assert repr(TextField()) == "<TypedField.text with options {}>"


def test_typedfield_to_field_type():
    @db.define
    class SomeTable(TypedTable):
        name = TypedField(str)  # basic mapping

    @db.define
    class OtherTable(TypedTable):
        second = ReferenceField(SomeTable)  # reference to TypedTable
        third = ReferenceField(db.some_table)  # reference to pydal table
        fourth = TypedField(list[str])  # generic alias
        optional_one = TypedField(typing.Optional[str])
        optional_two = TypedField(str | None)

    with pytest.raises(NotImplementedError):
        @db.define
        class Invalid(TypedTable):
            third = TypedField(dict[str, int])  # not supported


def test_fields():
    @db.define
    class SomeNewTable(TypedTable):
        name: str

    class OtherNewTable(TypedTable):
        name: str

    db.define(OtherNewTable)

    assert str(StringField()) == "TypedField.string"
    assert str(BlobField()) == "TypedField.blob"
    assert str(Boolean()) == "TypedField.boolean"
    assert str(IntegerField()) == "TypedField.integer"
    assert str(DoubleField()) == "TypedField.double"
    assert str(DecimalField(1, 1)) == "TypedField.decimal(1, 1)"
    assert str(DateField()) == "TypedField.date"
    assert str(TimeField()) == "TypedField.time"
    assert str(DatetimeField()) == "TypedField.datetime"
    assert str(PasswordField()) == "TypedField.password"
    assert str(UploadField()) == "TypedField.upload"
    assert str(ReferenceField("other")) == "TypedField.reference other"
    assert str(ReferenceField(db.some_new_table)) == "TypedField.reference some_new_table"
    assert str(ReferenceField(SomeNewTable)) == "TypedField.reference some_new_table"
    assert str(ReferenceField(OtherNewTable)) == "TypedField.reference other_new_table"
    with pytest.raises(ValueError):
        ReferenceField(object())

    assert str(ListStringField()) == "TypedField.list:string"
    assert str(ListIntegerField()) == "TypedField.list:integer"
    assert str(ListReferenceField("somenewtable")) == "TypedField.list:reference somenewtable"
    assert str(JSONField()) == "TypedField.json"
    assert str(BigintField()) == "TypedField.bigint"
