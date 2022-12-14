from pydal.objects import Set, Rows

from typedal import TypeDAL, TypedField, TypedTable, TypedRows, fields

import typing
from decimal import Decimal
import datetime as dt

from typedal.fields import TextField

db = TypeDAL("sqlite:memory")


### basic examples

@db.define
class Person(TypedTable):
    name: str

    # todo: let pycharm know age is actually int
    age: TypedField(int, default=18)
    nicknames: list[str]

    format = "%(name)s"

assert db.person._format == "%(name)s"

@db.define
class Pet(TypedTable):
    name: str
    owners: list[Person]


# delayed define (without decorator):
class Later(TypedTable):
    # date: date
    # time: time
    # datetime: datetime
    test: str


db.define(Later)

Person.insert(name="Henk", age=44, nicknames=["Henkie", "Gekke Henk"])
db.person.insert(name="Ingrid", nicknames=[])

henk: Person = db(Person.name == "Henk").select().first()
ingrid: Person = db(db.person.name == "Ingrid").select().first()
print(henk, ingrid)

Pet.insert(name="Max", owners=[henk, ingrid])

max = Pet(name="Max")
print(max)

people: TypedRows[Person] = db(Person).select()  # db(db.person.id > 0).select()

print(people.first())

for person in people:
    print(person.nicknames)


### example with all possible field types;

@db.define
class OtherTable(TypedTable):
    ...


# class AllFields(TypedTable):
#     # http://www.web2py.com/books/default/chapter/29/06/the-database-abstraction-layer#Field-types
#     string: str | TypedField(str)
#     text: TypedField(str, type="text")
#     blob: bytes | TypedField(bytes)
#     boolean: bool | TypedField(bool)
#     integer: int | TypedField(int)
#     double: float | TypedField(float)
#     decimal: Decimal | TypedField(Decimal, n=2, m=3)
#     date: dt.date | TypedField(dt.date)
#     time: dt.time | TypedField(dt.time)
#     datetime: dt.datetime | TypedField(dt.datetime)
#     password: TypedField(str, type="password")
#     upload: TypedField(str, uploadfield="upload_data")
#     upload_data: bytes | TypedField(bytes)
#     reference: OtherTable | TypedField(OtherTable)
#     list_string: list[str] | TypedField(list[str])
#     list_integer: list[int] | TypedField(list[int])
#     list_reference: list[OtherTable] | TypedField(list[OtherTable])
#     json: object | TypedField(object)
#     bigint: TypedField(int, type="bigint")


@db.define
class AllFieldsBasic(TypedTable):
    # http://www.web2py.com/books/default/chapter/29/06/the-database-abstraction-layer#Field-types
    string: typing.Optional[str]
    # string: str | None
    text: TypedField(str, type="text")
    blob: bytes
    boolean: bool
    integer: int
    double: float
    decimal: Decimal
    date: dt.date
    time: dt.time
    datetime: dt.datetime
    password: TypedField(str, type="password")
    upload: TypedField(str, type="upload", uploadfield="upload_data")
    upload_data: bytes
    reference: OtherTable
    reference_two: typing.Optional[db.other_table]
    list_string: list[str]
    list_integer: list[int]
    list_reference: list[OtherTable]
    json: object
    bigint: TypedField(int, type="bigint")


@db.define
class AllFieldsAdvanced(TypedTable):
    # http://www.web2py.com/books/default/chapter/29/06/the-database-abstraction-layer#Field-types

    # typing.Optional won't work on a TypedField! todo: document caveat
    string: TypedField(str, length=1000, notnull=False)
    text: TextField()
    blob: TypedField(bytes)
    boolean: TypedField(bool)
    integer: TypedField(int)
    double: TypedField(float)
    decimal: TypedField(Decimal, n=2, m=3)
    date: TypedField(dt.date)
    time: TypedField(dt.time)
    datetime: TypedField(dt.datetime)
    password: TypedField(str, type="password")
    upload: TypedField(str, type="upload", uploadfield="upload_data")
    upload_data: TypedField(bytes)
    reference: TypedField(OtherTable)
    reference_two: TypedField(db.other_table, notnull=False)
    list_string: TypedField(list[str])
    list_integer: TypedField(list[int])
    list_reference: TypedField(list[OtherTable])
    json: TypedField(object)
    bigint: TypedField(int, type="bigint")


@db.define
class AllFieldsExplicit(TypedTable):
    # http://www.web2py.com/books/default/chapter/29/06/the-database-abstraction-layer#Field-types

    # typing.Optional won't work on a TypedField! todo: document caveat
    string: fields.StringField(length=1000, notnull=False)
    text: fields.TextField()
    blob: fields.BlobField()
    boolean: fields.BooleanField()
    integer: fields.IntegerField()
    double: fields.DoubleField()
    decimal: fields.DecimalField(n=2, m=3)
    date: fields.DateField()
    time: fields.TimeField()
    datetime: fields.DatetimeField()
    password: fields.PasswordField()
    upload: fields.UploadField(uploadfield="upload_data")
    upload_data: fields.BlobField()
    reference: fields.ReferenceField("other_table")
    reference_two: fields.ReferenceField("other_table", notnull=False)
    list_string: fields.ListStringField()
    list_integer: fields.ListIntegerField()
    list_reference: fields.ListReferenceField('other_table')
    json: fields.JSONField()
    bigint: fields.BigintField()


xyz = AllFieldsExplicit.text

# todo: fix:
# for fname, ftype in AllFieldsBasic.__annotations__.items():
#     print(fname, repr(ftype))
#
# for fname, ftype in AllFieldsAdvanced.__annotations__.items():
#     print(fname, repr(ftype))

now = dt.datetime.utcnow()

db.other_table.insert()
db.other_table.insert()
other1 = db.other_table(id=1)
other2 = db.other_table(id=2)

with open('example_new.py', 'rb') as stream:
    db.all_fields_basic.insert(
        string="hi!",
        text="hi but longer",
        blob=b"\x23",
        boolean=True,
        integer=1,
        double=1.11111111111111111111111111111,
        decimal=1.11111111111111111111111111111,
        date=now.date(),
        time=now.time(),
        datetime=now,
        password="secret",
        upload=stream,
        upload_data=stream.read(),
        reference=other1,
        list_string=["hi", "there"],
        list_integer=[1, 2],
        list_reference=[other1, other2],
        json={'hi': 'there'},
        bigint=42,
    )

with open('example_new.py', 'rb') as stream:
    (
        AllFieldsAdvanced.insert(
            string="hi!",
            text="hi but longer",
            blob=b"\x23",
            boolean=True,
            integer=1,
            double=1.11111111111111111111111111111,
            decimal=1.11111111111111111111111111111,
            date=now.date(),
            time=now.time(),
            datetime=now,
            password="secret",
            upload=stream,
            upload_data=stream.read(),
            reference=other1,
            list_string=["hi", "there"],
            list_integer=[1, 2],
            list_reference=[other1, other2],
            json={'hi': 'there'},
            bigint=42,
        )
    )

with open('example_new.py', 'rb') as stream:
    AllFieldsExplicit.insert(
        string="hi!",
        text="hi but longer",
        blob=b"\x23",
        boolean=True,
        integer=1,
        double=1.11111111111111111111111111111,
        decimal=1.11111111111111111111111111111,
        date=now.date(),
        time=now.time(),
        datetime=now,
        password="secret",
        upload=stream,
        upload_data=stream.read(),
        reference=other1,
        list_string=["hi", "there"],
        list_integer=[1, 2],
        list_reference=[other1, other2],
        json={'hi': 'there'},
        bigint=42,
    )

rowa = db.all_fields_advanced(string="hi!")
print('advanced')
# for field in rowa:
#     print(field, type(rowa[field]))
print(rowa)

print('basic')
rowb = db.all_fields_basic(string="hi!")
# for field in rowa:
#     print(field, type(rowa[field]))
print(rowb)

print('explicit')
rowb = db.all_fields_explicit(string="hi!")
# for field in rowa:
#     print(field, type(rowa[field]))
print(rowb)
