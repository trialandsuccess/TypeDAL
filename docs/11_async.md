# 11. Async

Every method that talks to the database has an `*_async` twin: `collect_async()`, `insert_async()`,
`count_async()`, and so on. They do exactly what their sync counterparts do (same arguments, same
return values, same relationships, caching, hooks and permissions), except that they do not block
the event loop while the database is busy. The one exception is lazy loading, which has no async
form at all - see [Lazy loading is not async](#lazy-loading-is-not-async).

```python
from typedal import TypeDAL, TypedTable, TypedField

db = TypeDAL("postgres://user:pass@localhost/mydb")


@db.define()
class Author(TypedTable):
    name: TypedField[str]


async def handler():
    author = await Author.insert_async(name="Alice")
    authors = await Author.where(Author.name.startswith("A")).collect_async()
    total = await Author.count_async()
    return author, authors, total
```

## Transactions

One rule:

> A flat `*_async` call commits before it returns. A session is how you get a transaction.

```python
# three separate transactions, each already committed when the await returns:
await Author.insert_async(name="Alice")
await Author.insert_async(name="Bob")
await Author.insert_async(name="Carol")

# one transaction, committed at the end of the block:
async with db.session():
    await Author.insert_async(name="Alice")
    await Author.insert_async(name="Bob")
    await Author.insert_async(name="Carol")
```

If the block raises, the whole transaction is rolled back:

```python
async with db.session():
    await Author.insert_async(name="Alice")
    raise ValueError("never mind")  # Alice is not in the database
```

You can also settle a transaction yourself, mid-block; the next statement starts a new one:

```python
async with db.session() as session:
    await Author.insert_async(name="Alice")
    await session.commit()

    await Author.insert_async(name="Bob")
    await session.rollback()  # Bob is gone, Alice stays
```

`db.commit_async()` and `db.rollback_async()` do the same for the session the current task is in.
Outside a session they do nothing, because there is nothing left to settle.

### Sync code inside a session

`await session.run_sync(fn)` runs an ordinary *sync* function on the session's connection, inside
its transaction. This is the escape hatch for anything the async surface does not cover, and for
ORM behaviour that runs its own follow-up queries (lazy relationships, cache invalidation, hooks,
`ondelete="CASCADE"` fixups):

```python
def move_posts(from_author: int, to_author: int) -> int:
    posts = Post.where(Post.author == from_author).collect()
    for post in posts:
        post.update_record(author=to_author)
    return len(posts)


async with db.session() as session:
    moved = await session.run_sync(move_posts, alice.id, bob.id)
```

`db.run_sync(fn)` does the same outside a session: offloaded, and committed on return.

### Lazy loading is not async

Lazy loading is the one part of the ORM without an async twin, and it cannot get one: it is
triggered by *attribute access*, and attribute access cannot be awaited. So `post.author.name` or
`post.tags` after `first_async()` still issues its follow-up query on the calling thread, and in a
handler that thread is the event loop, which then stalls for the round trip - no error, and no
warning beyond the usual `lazy` one. Only the non-querying modes (`"forbid"`, `"warn"`, `"ignore"`;
see [4. Relationships](./4_relationships.md#lazy-loading-and-explicit-relationships)) are safe to
touch from a coroutine. For the rest there are two places to be: either join the relationship up
front (`Post.join("author", "tags").first_async()` - one query instead of N, async or not), or do
the access inside `run_sync`, where blocking is what the worker thread is for.

### Sessions belong to one task

A session lives in a `contextvar`, so the methods you call find it without you passing a handle
around. It belongs to the task that opened it, and **only** to that task:

```python
async with db.session():
    await Author.insert_async(name="Alice")  # in the session's transaction
    await asyncio.create_task(other())  # NOT in it - `other()` autocommits
```

That is deliberate. `create_task()` and `gather()` copy the context, so the session would
otherwise be inherited by every child task, and two tasks interleaving statements on one
connection is precisely the corruption a transaction exists to prevent. Children get their own
worker, their own connection, and flat autocommit semantics.

If you need concurrent work to share one transaction, do it the other way around: put the whole
unit in one `run_sync` callback.

Nesting `db.session()` inside an existing session in the same task joins the outer one: one
transaction, not two. There are no savepoints.

## Concurrency and connections

Async work runs on a small pool of worker threads. One worker is one pydal connection, so the pool
is bounded: it defaults to `max(4, pool_size)` and can be set per database.

```python
db = TypeDAL("postgres://...", pool_size=10, async_workers=10)
```

It is an ordinary config option, so `pyproject.toml`, `.env` and `TYPEDAL_ASYNC_WORKERS` set it too
(see [7. Configuration](./7_configuration.md)); the keyword above wins over all of them.

A session holds its worker for as long as it holds its transaction, so the number of *simultaneously
open* sessions cannot exceed `async_workers`; further sessions wait for one to be freed. Size the
pool to your concurrency, the way you would size any connection pool.

Flat calls borrow a worker per statement and give it straight back, so they need no headroom.

Threads, sessions and the event loop can all use the same `TypeDAL` at the same time; each has its
own connection, so nothing is shared and nothing needs guarding. That includes the thread-per-request
model py4web and web2py use.

`await db.close_async()` stops the worker threads and closes their connections. `db.close()` does it
too, so this is only needed when the database outlives its async usage.

### SQLite

SQLite allows one writer at a time; that is the database, not the engine. Two overlapping write
transactions (a long session plus another writer) will block or fail there exactly as they would
with plain threads. `sqlite:memory` is stricter still: pydal reaches it through shared-cache mode,
whose table locks turn a second connection away instead of waiting. For concurrent async work,
use a file-backed database, or `async_workers=1` to serialize it.

## Why thread offload

Three designs were on the table. This one runs pydal's own unmodified sync code on a worker thread,
pinning one thread (and therefore one connection, since pydal keeps its connection in a thread
local) per unit of work.

**Async driver with an execute-swap** (asyncpg/aiosqlite under a re-implemented statement path) was
tried first and abandoned. It means a second connection
with a second transaction inside one `TypeDAL`, which then has to be policed at runtime: every sync
statement must check whether the async side is holding uncommitted writes and vice versa. That guard
is unsound under threads, because pydal's connections are thread-local while the guard's state is
not: it refuses statements from unrelated threads that have their own connection, and lets genuinely
interleaved work through. It also cannot support anything that issues a follow-up query outside the
statement path: lazy relationships and the caching layer both fall back to blocking the loop.

**A greenlet bridge** (SQLAlchemy's `asyncio` layer) avoids the thread, but it means every call into
pydal has to run inside a greenlet-aware context and every blocking driver call has to be swapped
for an awaitable one: the same driver rewrite as above, plus a second control-flow mechanism, and
still no async driver for the backends pydal supports.

Thread offload buys the opposite trade: a thread per in-flight statement (cheap, bounded, and idle
while the database works) in exchange for pydal's semantics being *literally* pydal's semantics.
There is no second statement path to keep in sync, no version ceiling on pydal, and `run_sync` can
offer the entire sync ORM inside an async transaction, which neither alternative can.
