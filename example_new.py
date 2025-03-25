import datetime as dt
import typing
from decimal import Decimal

from pydal.validators import IS_NOT_EMPTY

from src.typedal import TypeDAL, TypedField, TypedRows, TypedTable, fields
from src.typedal.fields import TextField
from src.typedal.helpers import utcnow
from typedal.fields import TimestampField

db = TypeDAL("sqlite:memory")


### basic examples


@db.define(format="%(name)s")
class Person(TypedTable):
    name: TypedField[str]

    age = TypedField(int, default=18, requires=IS_NOT_EMPTY())
    nicknames: list[str]

    ts = TimestampField()


assert db.person._format == "%(name)s"


@db.define
class Pet(TypedTable):
    name: TypedField[str]
    owners: list[Person]


# delayed define (without decorator):
class Later(TypedTable):
    # date: date
    # time: time
    # datetime: datetime
    test: str


db.define(Later)

_henk = Person.insert(name="Henk", age=44, nicknames=["Henkie", "Gekke Henk"])
db.person.insert(name="Ingrid", nicknames=[])

henk: Person = db(Person.name == "Henk").select().first()
ingrid: Person = db(db.person.name == "Ingrid").select().first()
print(henk, ingrid)

assert ingrid
assert henk
assert _henk
assert henk.as_dict() == _henk.as_dict()

_max = Pet.insert(name="Max", owners=[henk, ingrid])

max = Pet(name="Max")
assert max.as_dict() == _max.as_dict()
print(max)

people = Person.select().collect()

# people: TypedRows[Person] = db(Person).select()  # db(db.person.id > 0).select()

print(people.first())

for person in people:
    print(person.nicknames)

# max ran away!
Pet.update_or_insert(Pet.name == "Max", owners=[])
# max = Pet.update_or_insert(Pet.name == "Max", owners=[])
max = Pet(name="Max")

assert not max.owners


### example with all possible field types;


@db.define
class OtherTable(TypedTable): ...


@db.define
class AllFieldsBasic(TypedTable):
    # http://www.web2py.com/books/default/chapter/29/06/the-database-abstraction-layer#Field-types
    string: typing.Optional[str]
    # string: str | None
    text = TypedField(str, type="text")
    blob: bytes
    boolean: bool
    integer: int
    double: float
    decimal: Decimal
    date: dt.date
    time: dt.time
    datetime: dt.datetime
    password = TypedField(str, type="password")
    upload = TypedField(str, type="upload", uploadfield="upload_data")
    upload_data: bytes
    reference: OtherTable
    reference_two: typing.Optional[db.other_table]  # type: ignore
    list_string: list[str]
    list_integer: list[int]
    list_reference: list[OtherTable]
    json: object
    bigint = TypedField(int, type="bigint")


@db.define
class AllFieldsAdvanced(TypedTable):
    # http://www.web2py.com/books/default/chapter/29/06/the-database-abstraction-layer#Field-types

    # typing.Optional won't work on a TypedField! todo: document caveat
    string = TypedField(str, length=1000, notnull=False)
    text = TextField()
    blob = TypedField(bytes)
    boolean = TypedField(bool)
    integer = TypedField(int)
    double = TypedField(float)
    decimal = TypedField(Decimal, n=2, m=3)
    date = TypedField(dt.date)
    time = TypedField(dt.time)
    datetime = TypedField(dt.datetime)
    password = TypedField(str, type="password")
    upload = TypedField(str, type="upload", uploadfield="upload_data")
    upload_data = TypedField(bytes)
    reference = TypedField(OtherTable)
    reference_two: int = TypedField(db.other_table, notnull=False)
    list_string = TypedField(list[str])
    list_integer = TypedField(list[int])
    list_reference = TypedField(list[OtherTable])
    json = TypedField(object)
    bigint = TypedField(int, type="bigint")


@db.define
class AllFieldsExplicit(TypedTable):
    # http://www.web2py.com/books/default/chapter/29/06/the-database-abstraction-layer#Field-types

    # typing.Optional won't work on a TypedField! todo: document caveat
    string = fields.StringField(length=1000, notnull=False)
    text = fields.TextField()
    blob = fields.BlobField()
    boolean = fields.BooleanField()
    integer = fields.IntegerField()
    double = fields.DoubleField()
    decimal = fields.DecimalField(n=2, m=3)
    date = fields.DateField()
    time = fields.TimeField()
    datetime = fields.DatetimeField()
    password = fields.PasswordField()
    upload = fields.UploadField(uploadfield="upload_data")
    upload_data = fields.BlobField()
    reference = fields.ReferenceField("other_table")
    reference_two = fields.ReferenceField("other_table", notnull=False)
    list_string = fields.ListStringField()
    list_integer = fields.ListIntegerField()
    list_reference = fields.ListReferenceField("other_table")
    json = fields.JSONField()
    bigint = fields.BigintField()


now = utcnow()

db.other_table.insert()
db.other_table.insert()
other1 = db.other_table(id=1)
other2 = db.other_table(id=2)

with open("example_new.py", "rb") as stream:
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
        json={"hi": "there"},
        bigint=42,
    )

with open("example_new.py", "rb") as stream:
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
            json={"hi": "there"},
            bigint=42,
        )
    )

with open("example_new.py", "rb") as stream:
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
        json={"hi": "there"},
        bigint=42,
    )

rowa = db.all_fields_advanced(string="hi!")
print("advanced")
# for field in rowa:
#     print(field, type(rowa[field]))
print(rowa)

print("basic")
rowb = db.all_fields_basic(string="hi!")
# for field in rowa:
#     print(field, type(rowa[field]))
print(rowb)

print("explicit")
rowb = db.all_fields_explicit(string="hi!")
# for field in rowa:
#     print(field, type(rowa[field]))
print(rowb)

counted = db(AllFieldsExplicit).count()

rows: TypedRows[AllFieldsExplicit] = db(AllFieldsExplicit).select()

for row in rows:
    print(row.id)
