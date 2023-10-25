# 3. Bulding Queries

Insert, update and delete work basically the same as in pydal. Select can be done in the same way, but TypeDAL 2.0 also
introduces the Query Builder, which provides a more modern abstraction to query your data.

## Insert

```python
# pydal:
db.person.insert(name="Bob")

# typedal:
Person.insert(name="Bob")
```

Where pydal returns the ID of the newly inserted row, TypeDAL will return a Person instance.

## Select

A Query Builder can be initialized by calling one of these methods on a TypedTable class:

- where
- select
- join
- paginate

e.g. `Person.where(...)` -> `QueryBuilder[Person]`

The query builder uses the builder pattern, so you can keep adding to it until you're ready to get the data:
`Person.where(Person.id > 0).where(Person.id < 99, Person.id == 101).select(Reference.id, Reference.title).join('reference').select(Person.ALL).paginate(limit=5, page=2)`

```sql
SELECT "person".*,
       "reference"."id",
       "reference"."title"
FROM "person",
     "reference"
WHERE (("person"."id" IN (SELECT "person"."id"
                          FROM "person"
                          WHERE ((("person"."id" > 0)) AND
                                 (("person"."id" < 99) OR ("person"."id" = 101)))
                          ORDER BY "person"."id" LIMIT 1 OFFSET 0))
  AND ("person"."reference" = "reference"."id") );
```

### where

...

### select

...

### join

...

### paginate

```python
.paginate(page=1, limit=5) # todo: alternative to collect?
```

Paginate transforms the more readable `page` and `limit` to pydals internal min and max.

Note: when using relationships, this limit is only applied to the 'main' table and any number of extra rows can be
loaded with relationship data!

### Collecting

The Query Builder has a few operations that don't return a new builder instance:

- count: get the number of rows this query matches
- collect: get a TypedRows result set of items matching your query, possibly with relationships loaded (if .join was
  used)
- collect_or_fail: where `collect` may return an empty result, this variant will raise an error if there are no results.
- first: get the first entity matching your query, possibly with relationships loaded (if .join was used)
- first_or_fail: where `first` may return an empty result, this variant will raise an error if there are no results.
- update: instead of selecting rows, update those matching the current query (see [Delete](#delete))
- delete: instead of selecting rows, delete those matching the current query (see [Update](#update))

Additionally, you can directly call `.all()`, `.collect()`, `.count()`, `.first()` on a model.

## Update

```python
# pydal:
db(db.person.id == "Old Name").update(name="New Name")

row = db.person(4)
row.update_record(name="New Name")

# typedal:
Person.where(id="Old Name").update(name="New Name")  # via query builder

person = Person(4)
person.update_record(name="New Name")
```

## Delete

```python
# pydal:
db(db.person.name == "Old Name").delete()

row = db.person(4)
row.update_record(name="New Name")

# typedal:
Person.where(id="Old Name").delete()  # via query builder

person = Person(4)
person.delete_record()
```
