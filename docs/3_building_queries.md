# 3. Building Queries

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
- cache

e.g. `Person.where(...)` -> `QueryBuilder[Person]`

The query builder uses the builder pattern, so you can keep adding to it (in any order) until you're ready to get the
data:

```python
Person
.where(Person.id > 0)
.where(Person.id < 99, Person.id == 101)
.select(Reference.id, Reference.title)
.join('reference')
.select(Person.ALL)
.paginate(limit=5, page=2)  # final step: actually runs the query
```

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

In pydal, this is the part that would be in `db(...)`.
Can be used in multiple ways:

- `.where(Query)` -> with a direct query such as `Table.id == 5`
- `.where(lambda table: table.id == 5)` -> with a query via a lambda
- `.where(id=5)` -> via keyword arguments

When using multiple where's, they will be ANDed:  
`.where(lambda table: table.id == 5).where(lambda table: table.id == 6)` equals `(table.id == 5) & (table.id=6)`  
When passing multiple queries to a single .where, they will be ORed:  
`.where(lambda table: table.id == 5, lambda table: table.id == 6)` equals `(table.id == 5) | (table.id=6)`

### select

Here you can enter any number of fields as arguments: database columns by name ('id'), by field reference (table.id) or
other (e.g. table.ALL).

```python
Person.select('id', Person.name, Person.ALL)  # defaults to Person.ALL if select is omitted.
```

You can also specify extra options such as `orderby` here. For supported keyword arguments, see
the [web2py docs](http://www.web2py.com/books/default/chapter/29/06/the-database-abstraction-layer#orderby-groupby-limitby-distinct-having-orderby_on_limitby-join-left-cache).

### join

Include relationship fields in the result.

`fields` can be names of Relationships on the current model.
If no fields are passed, all will be used.

By default, the `method` defined in the relationship is used.
This can be overwritten with the `method` keyword argument (left or inner)

```python
Person.join('articles', method='inner')  # will only yield persons that have related articles
```

### cache

```python
# all dependencies:
Person.cache()
# specific dependencies:
Person.cache(Person.id).cache(Article.id).join()
# same as above:
Person.cache(Person.id, Article.id).join()
# add an expire datetime:
Person.cache(expires_at=...)
# add an expire date via ttl (in seconds or timedelta):
Person.cache(ttl=60)
```

Queries can be cached using the `.cache` operator. Cached rows are saved using `dill` in the `typedal_cache` table.
It keeps track of dependencies to other tables. By default, this is all selected `id` columns (including joins).
You can specify specific id columns if you only want to have the cache depend on those.
When some of the underlying data changes, the cache entry is invalidated and the data will be loaded fresh from the
database when the query is next executed. When an `expire_at` or `ttl` is provided, data is also invalidated after that
time.

```python
@db.define(cache_dependency=False)
class ...
```

In order to enable this functionality, TypeDAL adds a `before update` and `before delete` hook to your tables, 
which manages the dependencies. You can disable this behavior by passing `cache_dependency=False` to `db.define`.
Be aware doing this might break some caching functionality!

### Collecting

The Query Builder has a few operations that don't return a new builder instance:

- count: get the number of rows this query matches
- collect: get a TypedRows result set of items matching your query, possibly with relationships loaded (if .join was
  used). TypedRows is almost the same as
  pydal [Rows](http://www.web2py.com/books/default/chapter/29/06/the-database-abstraction-layer#select), except they can
  be indexed by ID instead of a list index (e.g. `rows[15]` to get the row with ID 15)
- paginate: this works similarly to `collect`, but returns a PaginatedRows instead, which has a `.next()`
  and `.previous()` method to easily load more pages.
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
