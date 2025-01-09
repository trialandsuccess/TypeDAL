import datetime

from pydal import DAL, Field

from typedal.helpers import utcnow

db = DAL("sqlite:memory")

### basic examples

db.define_table(
    "person", Field("name", "string"), Field("age", "integer", default=18), Field("nicknames", "list:string")
)

db.define_table("pet", Field("name", "string"), Field("owners", "list:reference person"))

db.person.insert(name="Henk", age=44, nicknames=["Henkie", "Gekke Henk"])
db.person.insert(name="Ingrid", age=47, nicknames=[])

henk = db(db.person.name == "Henk").select().first()
ingrid = db(db.person.name == "Ingrid").select().first()
print(henk, ingrid)

db.pet.insert(name="Max", owners=[henk, ingrid])

max = db.pet(name="Max")
print(max)

people = db(db.person.id > 0).select()

for person in people:
    print(person.name)

### example with all possible field types;
db.define_table("other_table")

db.define_table(
    "all_fields",
    Field("string", "string"),
    Field("text", "text"),
    Field("blob", "blob"),
    Field("boolean", "boolean"),
    Field("integer", "integer"),
    Field("double", "double"),
    Field("decimal", "decimal(2,3)"),
    Field("date", "date"),
    Field("time", "time"),
    Field("datetime", "datetime"),
    Field("password", "password"),
    Field("upload", "upload", uploadfield="upload_data"),
    Field("upload_data", "blob"),
    Field("reference", "reference other_table"),
    Field("list_string", "list:string"),
    Field("list_integer", "list:integer"),
    Field("list_reference", "list:reference other_table"),
    Field("json", "json"),
    Field("bigint", "bigint"),
    # The big-id and, big-reference are only supported by some of the database engines and are experimental.
)

now = utcnow()

db.other_table.insert()
db.other_table.insert()
other1 = db.other_table(id=1)
other2 = db.other_table(id=2)

with open("example_old.py", "rb") as stream:
    db.all_fields.insert(
        string="hi",
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

row = db.all_fields(string="hi")
for field in row:
    print(field, type(row[field]))
