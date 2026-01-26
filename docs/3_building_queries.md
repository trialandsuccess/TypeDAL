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
- groupby
- having
- cache

e.g. `Person.where(...)` -> `QueryBuilder[Person]`

The query builder uses the builder pattern, so you can keep adding to it (in any order) until you're ready to get the
data:

```python
builder = Person
.where(Person.id > 0)
.where(Person.id < 99, Person.id == 101)  # id < 99 OR id == 101
.select(Reference.id, Reference.title)
.join('reference')
.select(Person.ALL)

# final step: actually runs the query:
builder.paginate(limit=5, page=2)

# to get the SQL (for debugging or subselects), you can use:
builder.to_sql()
```

```sql
SELECT "person".*
     , "reference"."id"
     , "reference"."title"
    FROM "person"
       , "reference"
    WHERE (("person"."id" IN (SELECT "person"."id"
                                  FROM "person"
                                  WHERE ((("person"."id" > 0)) AND
                                         (("person"."id" < 99) OR ("person"."id" = 101)))
                                  ORDER BY "person"."id"
                                  LIMIT 1 OFFSET 0))
        AND ("person"."reference" = "reference"."id"));
```

### where

In pydal, this is the part that would be in `db(...)`.
Can be used in multiple ways:

- `.where(query)` -> with a direct query such as `query = (Table.id == 5)`
- `.where(lambda table: table.id == 5)` -> with a query via a lambda
- `.where(id=5)` -> via keyword arguments
- `.where({"id": 5})` -> via a dictionary (equivalent to keyword args)
- `.where(Table.field)` -> with a Field directly, checks if field is not null

When using multiple `.where()` calls, they will be ANDed together:
`.where(lambda table: table.id == 5).where(active=True)` equals `(table.id == 5) & (table.active == True)`

When passing multiple arguments to a single `.where()`, they will be ORed:
`.where({"id": 5}, {"id": 6})` equals `(table.id == 5) | (table.id == 6)`

### select

Here you can enter any number of fields as arguments: database columns by name ('id'), by field reference (Table.id),
other (e.g. Table.ALL), or Expression objects.

```python
Person.select('id', Person.name, Person.ALL)  # defaults to Person.ALL if select is omitted.
```

You can also specify extra options as keyword arguments. Supported options are: `orderby`, `groupby`, `limitby`,
`distinct`, `having`, `orderby_on_limitby`, `join`, `left`, `cache`, see also
the [web2py docs](http://www.web2py.com/books/default/chapter/29/06/the-database-abstraction-layer#orderby-groupby-limitby-distinct-having-orderby_on_limitby-join-left-cache).

```python
Person.select(Person.name, distinct=True)
```

If you only want a list of name strings (in this example) instead of Person instances, you could use the column() method
instead:

```python
Person.column(Person.name, distinct=True)
```

You can use `.orderby(*fields)` as an alternative to `select(orderby=...)`:

```python
Person.where(...).orderby(~Person.name, "age")
```

`.orderby()` accepts field references (`Table.field` or `"field_name""`), reverse ordering (`~Table.field`), or the
literal `"<random>"`. Multiple field references can be passed (except when using `<random>`).

#### Raw SQL Expressions

For complex SQL that can't be expressed with field references, use `sql_expression()`:

```python
# Simple arithmetic
expr = db.sql_expression("age * 2")
Person.select(expr)

# Safe parameter injection with t-strings (Python 3.14+)
min_age = 21
expr = db.sql_expression(t"age >= {min_age}", output_type="boolean")
Person.where(expr).select()

# Positional arguments
expr = db.sql_expression("age > %s AND status = %s", 18, "active", output_type="boolean")
Person.where(expr).select()

# Named arguments
expr = db.sql_expression(
    "EXTRACT(year FROM %(date_col)s) = %(year)s",
    date_col="created_at",
    year=2023,
    output_type="boolean"
)
Person.where(expr).select()
```

Expressions can be used in `where()`, `select()`, `orderby()`, and other query methods.

### join

Include relationship fields in the result.

`fields` can be names of Relationships on the current model.
If no fields are passed, all will be used.

By default, the `method` defined in the relationship is used.
This can be overwritten with the `method` keyword argument (left or inner)

```python
Person.join('articles', method='inner')  # will only yield persons that have related articles
```

For more details about relationships and joins, see [4. Relationships](./4_relationships.md).

### groupby & having

Group query results by one or more fields, typically used with aggregate functions like `count()`, `sum()`, `avg()`, etc.
Use `having` to filter the grouped results based on aggregate conditions.

```python
# Basic grouping: count articles per author
Article.select(Article.author, Article.id.count().with_alias("article_count"))
    .groupby(Article.author)
    .collect()

# Group by multiple fields
Sale.select(Sale.product, Sale.region, Sale.amount.sum().with_alias("total"))
    .groupby(Sale.product, Sale.region)
    .collect()

# Filter groups with having: only authors with more than 5 articles
Article.select(Article.author, Article.id.count().with_alias("article_count"))
    .groupby(Article.author)
    .having(Article.id.count() > 5)
    .collect()

# Can be chained in any order
School.groupby(School.id)
    .having(Team.id.count() > 0)
    .select(School.id, Team.id.count())
    .collect()
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

**Note:** For caching function results (instead of just query results), see [9. Function Memoization](./9_memoization.md).

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
- execute: get the raw rows matching your query as returned by pydal, without entity mapping or relationship loading.
  Useful for subqueries or when you need lower-level control.
- first: get the first entity matching your query, possibly with relationships loaded (if .join was used)
- first_or_fail: where `first` may return an empty result, this variant will raise an error if there are no results.
- to_sql: get the SQL string that would run, useful for debugging, subqueries and other advanced SQL operations.
- update: instead of selecting rows, update those matching the current query (see [Delete](#delete))
- delete: instead of selecting rows, delete those matching the current query (see [Update](#update))

Additionally, you can directly call `.all()`, `.collect()`, `.count()`, `.first()` on a model (e.g. `User.all()`).

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
