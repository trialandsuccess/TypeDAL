"""
Core functionality of TypeDAL.
"""

from __future__ import annotations

# noinspection PyUnusedImports
import asyncio
import collections
import contextlib
import datetime as dt
import sys
import typing as t
import warnings
from pathlib import Path

import pydal

from .async_execution import (
    DELETE_STRATEGIES,
    LASTROWID_STRATEGIES,
    WRITE_STATEMENTS,
    AsyncConnectionPool,
    AsyncPoolManager,
    ConcurrentTransactionError,
    SyncTransactionTracker,
    TransactionSplitError,
)
from .config import LazyPolicy, TypeDALConfig, load_config
from .helpers import (
    SYSTEM_SUPPORTS_TEMPLATES,
    default_representer,
    sql_escape_template,
    sql_expression,
    to_snake,
)
from .serializers.typescript import TypedDictRegistry

# noinspection PyUnusedImports
from .types import CacheStatus, Field, Template

try:
    # python 3.14+
    from annotationlib import ForwardRef
except ImportError:  # pragma: no cover
    # python 3.13-
    from typing import ForwardRef  # special case, keep `from typing`

if t.TYPE_CHECKING:
    from .fields import TypedField
    from .query_builder import QueryBuilder
    from .types import AnyDict, DefineKwargs, Expression, Rows, Set, T_Query, Table


# stands in for the task in `TypeDAL._async_pending_owners` when there isn't one. Its own
# object rather than None so it cannot collide with a real entry, and so the "is this owner
# still running?" test can special-case it explicitly instead of by falsiness.
NO_ASYNC_TASK = object()


def _expression_subclasses() -> t.Iterator[type]:
    """
    Yield pydal.objects.Expression and every (nested) subclass currently loaded, e.g. Field and TypedField.
    """
    seen = {pydal.objects.Expression}
    stack = [pydal.objects.Expression]
    while stack:
        for subclass in stack.pop().__subclasses__():
            if subclass not in seen:
                seen.add(subclass)
                stack.append(subclass)

    return iter(seen)


def _purge_dialect_expressions(adapter: t.Any) -> None:
    """
    Undo pydal's global dialect-expression registration for a closed adapter.

    Some dialects (currently Postgres and Snowflake) register extra Expression methods
    (e.g. `.dow`, `.doy`) by stashing a wrapper per name in the process-wide, class-level
    `Expression._dialect_expressions_` dict. `Expression.__new__` then copies each wrapper
    onto every instantiated Expression subclass (Field, TypedField, ...) as a bound method.
    Both of those are permanent references living outside of any object graph tied to a
    specific db, so they are not reference cycles: closing the adapter and even running
    `gc.collect()` will not free it, and with it the entire db (tables, fields, models)
    it belongs to, for the lifetime of the process, unless explicitly undone here.
    """
    expressions = pydal.objects.Expression._dialect_expressions_
    stale = [
        name
        for name, wrapper in expressions.items()
        if getattr(getattr(wrapper, "dialect", None), "adapter", None) is adapter
    ]

    if not stale:
        return

    for name in stale:
        del expressions[name]

    for cls in _expression_subclasses():
        for name in stale:
            if name in cls.__dict__:
                delattr(cls, name)


# note: these functions can not be moved to a different file,
#  because then they will have different globals and it breaks!


def evaluate_forward_reference_312(fw_ref: ForwardRef, namespace: dict[str, type]) -> type:  # pragma: no cover
    """
    Extract the original type from a forward reference string.

    Variant for python 3.12 and below
    """
    return t.cast(
        type,
        fw_ref._evaluate(  # ty: ignore[deprecated]
            localns=locals(),
            globalns=globals() | namespace,
            recursive_guard=frozenset(),
        ),
    )


def evaluate_forward_reference_313(fw_ref: ForwardRef, namespace: dict[str, type]) -> type:  # pragma: no cover
    """
    Extract the original type from a forward reference string.

    Variant for python 3.13
    """
    return t.cast(
        type,
        fw_ref._evaluate(  # ty: ignore[deprecated]
            localns=locals(),
            globalns=globals() | namespace,
            recursive_guard=frozenset(),
            type_params=(),  # suggested since 3.13 (warning) and not supported before. Mandatory after 1.15!
        ),
    )


def evaluate_forward_reference_314(fw_ref: ForwardRef, namespace: dict[str, type]) -> type:  # pragma: no cover
    """
    Extract the original type from a forward reference string.

    Variant for python 3.14 (and hopefully above)
    """
    return t.cast(
        type,
        fw_ref.evaluate(
            locals=locals(),
            globals=globals() | namespace,
            type_params=(),
        ),
    )


def evaluate_forward_reference(
    fw_ref: ForwardRef,
    namespace: dict[str, type] | None = None,
) -> type:  # pragma: no cover
    """
    Extract the original type from a forward reference string.

    Automatically chooses strategy based on current Python version.
    """
    if sys.version_info.minor < 13:
        return evaluate_forward_reference_312(fw_ref, namespace=namespace or {})
    elif sys.version_info.minor == 13:
        return evaluate_forward_reference_313(fw_ref, namespace=namespace or {})
    else:
        return evaluate_forward_reference_314(fw_ref, namespace=namespace or {})


def resolve_annotation_313(ftype: str, namespace: dict[str, type] | None = None) -> type:  # pragma: no cover
    """
    Resolve an annotation that's in string representation.

    Variant for Python 3.13
    """
    fw_ref: ForwardRef = t.get_args(t.Type[ftype])[0]  # ty: ignore[invalid-type-form]
    return evaluate_forward_reference(fw_ref, namespace=namespace)


def resolve_annotation_314(ftype: str, namespace: dict[str, type] | None = None) -> type:  # pragma: no cover
    """
    Resolve an annotation that's in string representation.

    Variant for Python 3.14 + using annotationlib
    """
    fw_ref = ForwardRef(ftype)
    return evaluate_forward_reference(fw_ref, namespace=namespace)


def resolve_annotation(ftype: str, namespace: dict[str, type] | None = None) -> type:  # pragma: no cover
    """
    Resolve an annotation that's in string representation.

    Automatically chooses strategy based on current Python version.
    """
    if sys.version_info.major != 3:
        raise EnvironmentError("Only python 3 is supported.")
    elif sys.version_info.minor <= 13:
        return resolve_annotation_313(ftype, namespace=namespace)
    else:
        return resolve_annotation_314(ftype, namespace=namespace)


if t.TYPE_CHECKING:

    class _TypeDALBase:
        # attributes accessed throughout the codebase
        _adapter: t.Any
        _migrate: t.Any
        representers: t.Any

        def __init__(self, *args: t.Any, **kwargs: t.Any) -> None: ...

        def __call__(self, query: t.Any = None) -> "Set": ...

        def commit(self) -> None: ...

        def rollback(self) -> None: ...

        def define_table(self, *args: t.Any, **kwargs: t.Any) -> "Table": ...

        def has_representer(self, field_type: str) -> bool: ...

        # pydal exposes dynamic table attributes like `db.my_table`.
        # this keeps type checkers from flagging these as missing attributes.
        def __getattr__(self, item: str) -> "Table": ...

else:

    class _TypeDALBase(pydal.DAL):
        pass


class TypeDAL(_TypeDALBase):
    """
    Drop-in replacement for pyDAL with layer to convert class-based table definitions to classical pydal define_tables.
    """

    _config: TypeDALConfig
    _builder: TableDefinitionBuilder

    # appended to, not replaced: pydal's own TimingHandler is what fills `db._timings`, and
    # dropping it would take that with it.
    execution_handlers = [*pydal.DAL.execution_handlers, SyncTransactionTracker]  # noqa: RUF012

    # whether each of the two connections holds an open transaction. See `TransactionSplitError`
    # for why the pair has to be tracked at all.
    #
    # The two are shaped differently on purpose. pydal's sync connection really is one shared
    # thing - `THREAD_LOCAL` gives one per thread, and every coroutine on an event loop is the
    # same thread - so a single flag describes it exactly. The async side keeps a connection
    # *per task* (`PostgresAsyncPool`), so "has uncommitted writes" is a per-task fact and a
    # single flag cannot hold it: one task's `commit_async()` would clear it on behalf of every
    # other task, and the guard would then wave through exactly the read it exists to refuse.
    _sync_pending: bool
    _async_pending_owners: set[t.Any]

    # similar to the insert/update/delete hooks at table-level but for .collect/.execute:
    # note: return values are ignored!
    _before_collect: list[t.Callable[["QueryBuilder[t.Any]"], None]]
    _after_collect: list[t.Callable[["QueryBuilder[t.Any]", "TypedRows[t.Any]", "Rows"], None]]
    _before_execute: list[t.Callable[["QueryBuilder[t.Any]"], None]]
    _after_execute: list[t.Callable[["QueryBuilder[t.Any]", "Rows"], None]]

    def __init__(
        self,
        uri: str | None = None,  # default from config or 'sqlite:memory'
        pool_size: int | None = None,  # default 1 if sqlite else 3
        folder: str | Path | None = None,  # default 'databases' in config
        db_codec: str = "UTF-8",
        check_reserved: list[str] | None = None,
        migrate: bool | None = None,  # default True by config
        fake_migrate: bool | None = None,  # default False by config
        migrate_enabled: bool = True,
        fake_migrate_all: bool = False,
        decode_credentials: bool = False,
        driver_args: AnyDict | None = None,
        adapter_args: AnyDict | None = None,
        attempts: int = 5,
        auto_import: bool = False,
        bigint_id: bool = False,
        debug: bool = False,
        lazy_tables: bool = False,
        db_uid: str | None = None,
        after_connection: t.Callable[..., t.Any] | None = None,
        tables: list[str] | None = None,
        ignore_field_case: bool = True,
        entity_quoting: bool = True,
        table_hash: str | None = None,
        enable_typedal_caching: bool | None = None,
        use_pyproject: bool | str = True,
        use_env: bool | str = True,
        connection: str | None = None,
        config: TypeDALConfig | None = None,
        lazy_policy: LazyPolicy | None = None,
    ) -> None:
        """
        Adds some internal tables after calling pydal's default init.

        Set enable_typedal_caching to False to disable this behavior.
        """
        config = config or load_config(connection, _use_pyproject=use_pyproject, _use_env=use_env)
        config.update(
            database=uri,
            dialect=uri.split(":")[0] if uri and ":" in uri else None,
            folder=str(folder) if folder is not None else None,
            migrate=migrate,
            fake_migrate=fake_migrate,
            caching=enable_typedal_caching,
            pool_size=pool_size,
            lazy_policy=lazy_policy,
        )

        self._config = config
        self.db = self
        self._builder = TableDefinitionBuilder(self)

        self._before_collect = []
        self._after_collect = []
        self._before_execute = []
        self._after_execute = []
        self._async_pools = AsyncPoolManager(self)  # lazily-opened async connection; see _get_async_pool

        # set before super().__init__(), which migrates and therefore already executes
        # statements through SyncTransactionTracker.
        self._sync_pending = False
        self._async_pending_owners = set()

        if config.folder:
            Path(config.folder).mkdir(exist_ok=True)

        super().__init__(
            config.database,
            config.pool_size,
            config.folder,
            db_codec,
            check_reserved,
            config.migrate,
            config.fake_migrate,
            migrate_enabled,
            fake_migrate_all,
            decode_credentials,
            driver_args,
            adapter_args,
            attempts,
            auto_import,
            bigint_id,
            debug,
            lazy_tables,
            db_uid,
            after_connection,
            tables,
            ignore_field_case,
            entity_quoting,
            table_hash,
        )

        if config.caching:
            self.try_define(_TypedalCache)
            self.try_define(_TypedalCacheDependency)

    def commit(self) -> None:
        """
        Commit the transaction on pydal's own (synchronous) connection.

        Says nothing about the async connection - that one is ended by `commit_async()`. What it
        does do is clear the flag that blocks the async path, so committing here is how you make
        the other side usable again after a sync write.
        """
        super().commit()
        self._sync_pending = False

    def rollback(self) -> None:
        """
        Roll back the transaction on pydal's own (synchronous) connection. See `commit`.
        """
        super().rollback()
        self._sync_pending = False

    def close(self) -> None:
        """Close the database connection and unbind all defined TypedTable models."""
        adapter = self._adapter
        try:
            super().close()  # ty: ignore[unresolved-attribute]
        finally:
            for model in set(self._builder.class_map.values()):
                model.unbind()
            self._builder.class_map.clear()

            if adapter is not None:
                adapter.db = None
                _purge_dialect_expressions(adapter)

            for table_name in tuple(getattr(self, "tables", ())):
                table: Table | None = getattr(self, table_name, None)
                if table is None:  # pragma: no cover
                    continue

                for hook_name in (
                    "_before_insert",
                    "_after_insert",
                    "_before_update",
                    "_after_update",
                    "_before_delete",
                    "_after_delete",
                ):
                    hooks = getattr(table, hook_name, None)
                    if isinstance(hooks, list):
                        hooks.clear()

                if hasattr(table, "_db"):
                    table._db = None

                for field_name in getattr(table, "fields", ()):
                    field: Field | None = getattr(table, field_name, None)
                    if field is None:  # pragma: no cover
                        continue

                    if hasattr(field, "_db"):
                        field._db = None
                    if hasattr(field, "db"):
                        field.db = None
                    if hasattr(field, "table"):
                        field.table = None
                    if hasattr(field, "_table"):
                        field._table = None
                    if hasattr(field, "requires"):
                        field.requires = []

    def try_define[T: t.Any](self, model: t.Type[T], verbose: bool = False) -> t.Type[T]:
        """
        Try to define a model with migrate or fall back to fake migrate.
        """
        try:
            return self.define(model, migrate=self._migrate)
        except Exception as e:
            # clean up:
            self.rollback()
            if (tablename := self.to_snake(model.__name__)) and tablename in dir(self):
                delattr(self, tablename)

            if verbose:
                warnings.warn(f"{model} could not be migrated, try faking", source=e, category=RuntimeWarning)

            # try again:
            return self.define(model, migrate=self._migrate, fake_migrate=self._migrate, redefine=True)

    default_kwargs: t.ClassVar[AnyDict] = {
        # fields are 'required' (notnull) by default:
        "notnull": True,
    }

    @t.overload
    def define[T: t.Any](
        self,
        maybe_cls: None = None,
        **kwargs: t.Unpack[DefineKwargs],
    ) -> t.Callable[[t.Type[T]], t.Type[T]]:
        """
        Typing Overload for define without a class.

        @db.define()
        class MyTable(TypedTable): ...
        """

    @t.overload
    def define[T: t.Any](self, maybe_cls: t.Type[T], **kwargs: t.Unpack[DefineKwargs]) -> t.Type[T]:
        """
        Typing Overload for define with a class.

        @db.define
        class MyTable(TypedTable): ...
        """

    def define[T: t.Any](
        self,
        maybe_cls: t.Type[T] | None = None,
        **kwargs: t.Unpack[DefineKwargs],
    ) -> t.Type[T] | t.Callable[[t.Type[T]], t.Type[T]]:
        """
        Can be used as a decorator on a class that inherits `TypedTable`, \
          or as a regular method if you need to define your classes before you have access to a 'db' instance.

        You can also pass extra arguments to db.define_table.
            See http://www.web2py.com/books/default/chapter/29/06/the-database-abstraction-layer#Table-constructor

        Example:
            @db.define
            class Person(TypedTable):
                ...

            class Article(TypedTable):
                ...

            # at a later time:
            db.define(Article)

        Returns:
            the result of pydal.define_table
        """

        def wrapper(cls: t.Type[T]) -> t.Type[T]:
            """Define and return a TypedTable class bound to this DB instance."""
            return self._builder.define(cls, **kwargs)

        if maybe_cls:
            return wrapper(maybe_cls)

        return wrapper

    def __call__(self, *_args: T_Query, **kwargs: t.Any) -> "TypedSet":  # ty: ignore[invalid-method-override]
        """
        A db instance can be called directly to perform a query.

        Usually, only a query is passed.

        Example:
            db(query).select()

        """
        args = list(_args)
        if args:
            cls = args[0]
            if isinstance(cls, bool):
                raise ValueError("Don't actually pass a bool to db()! Use a query instead.")

            if isinstance(cls, type) and issubclass(type(cls), type) and issubclass(cls, TypedTable):
                # table defined without @db.define decorator!
                _cls: t.Type[TypedTable] = cls
                args[0] = _cls.id != None

        _set = super().__call__(*args, **kwargs)
        return t.cast(TypedSet, _set)

    def __getitem__(self, key: str) -> "Table":
        """
        Allows dynamically accessing a table by its name as a string.

        If you need the TypedTable class instead of the pydal table, use find_model instead.

        Example:
            db['users'] -> user
        """
        return t.cast(Table, super().__getitem__(str(key)))  # ty: ignore[unresolved-attribute]

    def find_model(self, table_name: str) -> t.Type["TypedTable"] | None:
        """
        Retrieves a mapped table class by its name.

        This method searches for a table class matching the given table name
        in the defined class map dictionary. If a match is found, the corresponding
        table class is returned; otherwise, None is returned, indicating that no
        table class matches the input name.

        Args:
            table_name: The rname of the table to retrieve the mapped class for.

        Returns:
            The mapped table class if it exists, otherwise None.
        """
        return self._builder.class_map.get(table_name, None)

    @property
    def _class_map(self) -> dict[str, t.Type["TypedTable"]]:
        # alias for backward-compatibility
        return self._builder.class_map

    def _known_classes(self) -> dict[str, t.Type["TypedTable"]]:
        """
        Return currently defined TypedTable classes keyed by class name.

        Useful when resolving forward references in annotations/relationships.
        """
        return {table.__name__: table for table in self._class_map.values()}

    @staticmethod
    def to_snake(camel: str) -> str:
        """
        Moved to helpers, kept as a static method for legacy reasons.
        """
        return to_snake(camel)

    def executesql(
        self,
        query: str | Template,
        placeholders: t.Iterable[str] | dict[str, str] | None = None,
        as_dict: bool = False,
        fields: "Field | TypedField[t.Any] | Table | t.Iterable[Field | TypedField[t.Any]] | None" = None,
        colnames: t.Iterable[str] | None = None,
        as_ordered_dict: bool = False,
    ) -> list[t.Any] | None:
        """
        Executes a raw SQL statement or a TypeDAL template query.

        If `query` is provided as a `Template` and the system supports template
        rendering, it will be processed with `sql_escape_template` before being
        executed. Otherwise, the query is passed to the underlying DAL as-is.

        Args:
            query (str | Template): The SQL query to execute, either a plain
                string or a `Template` (created via the `t""` syntax).
            placeholders (Iterable[str] | dict[str, str] | None, optional):
                Parameters to substitute into the SQL statement. Can be a sequence
                (for positional parameters) or a dictionary (for named parameters).
                Usually not applicable when using a t-string, since template
                expressions handle interpolation directly.
            as_dict (bool, optional): If True, return rows as dictionaries keyed by
                column name. Defaults to False.
            fields (Iterable[Field | TypedField] | None, optional): Explicit set of
                fields to map results onto. Defaults to None.
            colnames (Iterable[str] | None, optional): Explicit column names to use
                in the result set. Defaults to None.
            as_ordered_dict (bool, optional): If True, return rows as `OrderedDict`s
                preserving column order. Defaults to False.

        Returns:
            list[t.Any]: The query result set. Typically a list of tuples if
            `as_dict` and `as_ordered_dict` are False, or a list of dict-like
            objects if those flags are enabled.
        """
        if SYSTEM_SUPPORTS_TEMPLATES and isinstance(query, Template):  # pragma: no cover
            query = sql_escape_template(self, query)

        rows: list[t.Any] = super().executesql(  # ty: ignore[unresolved-attribute]
            query,
            placeholders=placeholders,
            as_dict=as_dict,
            fields=fields,
            colnames=colnames,
            as_ordered_dict=as_ordered_dict,
        )

        return rows

    # ------------------------------------------------------------------
    # Async execution path.
    # ------------------------------------------------------------------

    async def _get_async_pool(self) -> AsyncConnectionPool:
        """
        The async connection (a real pool for Postgres, a single wrapped connection for SQLite)
        for this instance, opened on first use.

        Necessarily a separate connection from pydal's own thread-local sync connection - pydal
        drives Postgres with psycopg2 and SQLite with sqlite3, neither of which can be awaited -
        and therefore a separate transaction. Rather than let a read silently miss the other
        side's uncommitted work, this refuses to run while the sync side has any; the reasoning
        is in `TransactionSplitError`.

        The lifecycle itself lives in `AsyncPoolManager` (async_execution.py).
        """
        if self._sync_pending:
            raise TransactionSplitError(
                "The synchronous connection has uncommitted writes, which this async statement "
                "would not see. Call `db.commit()` or `db.rollback()` first.",
            )

        return await self._async_pools.get()

    def _async_pending_owner(self) -> t.Any:
        """
        The key the calling task's pending async writes are recorded under.

        The task itself where there is one. `asyncio.current_task()` answers None for a
        coroutine driven without one, which is rare but not impossible; `NO_ASYNC_TASK` keeps
        those recorded rather than silently untracked, at the cost of only being cleared by an
        explicit `commit_async()`/`rollback_async()` - there is no task whose end could stand
        in for that.
        """
        return asyncio.current_task() or NO_ASYNC_TASK

    @contextlib.contextmanager
    def _mark_async_pending(self) -> t.Iterator[None]:
        """
        Mark this task as holding an async write for the duration of the write.

        Entered before connection acquisition so a synchronous write from another coroutine
        cannot slip in during the await. Only `ConcurrentTransactionError` un-marks because
        that caller was refused before opening a transaction; any other failure may have left
        one open.
        """
        owner = self._async_pending_owner()
        # an earlier `_async` write in this same task already marked it, and its transaction is
        # still open - this block's failure says nothing about that one, so leave it recorded.
        already_pending = owner in self._async_pending_owners

        self._async_pending_owners.add(owner)
        try:
            yield
        except ConcurrentTransactionError:
            if not already_pending:
                self._async_pending_owners.discard(owner)
            raise

    async def _release_readonly_connection(self, pool: AsyncConnectionPool) -> None:
        """
        Release a connection used only for reading, unless this task has pending writes.

        Rollback is the pool-level release operation. Failures are suppressed because this is
        `finally` cleanup and the task callback can still reclaim a Postgres connection.
        """
        if self._async_pending_owner() in self._async_pending_owners:
            return

        with contextlib.suppress(Exception):
            await pool.rollback()

    def _settle_abandoned_async_writes(self) -> bool:
        """
        Reclaim an abandoned `sqlite:memory` async transaction, if there is one.

        Only the sync side needs this: pydal's `ExecutionHandler` cannot await the async pool,
        and `sqlite:memory` has no connection to hand back. The two per-task backends already
        reclaim their abandoned connections through their own done-callbacks.

        Returns False when the reclaim could not be completed. The caller must then treat the
        async side as still pending, so the sync statement raises `TransactionSplitError`
        instead of walking into a driver lock.
        """
        pool = self._async_pools.pool
        return pool is None or pool.settle_abandoned_sync()

    def _has_pending_async_writes(self) -> bool:
        """
        Whether any task holds uncommitted async writes.

        Reclaim a finished `sqlite:memory` owner before checking. If reclaim fails, retain the
        pending state; otherwise discard finished tasks whose abandoned writes were rolled back.
        """
        if not self._settle_abandoned_async_writes():
            return True

        self._async_pending_owners = {
            owner for owner in self._async_pending_owners if owner is NO_ASYNC_TASK or not owner.done()
        }

        return bool(self._async_pending_owners)

    async def select_async(
        self,
        query: pydal.objects.Query,
        *fields: t.Any,
        **attributes: t.Any,
    ) -> pydal.objects.Rows:
        """
        Async twin of `db(query).select(*fields, **attributes)`.

        Mirrors `Set.select()` (pydal objects.py) and `SQLAdapter.select()`/
        `_select_aux()` (adapters/base.py): build via pydal's own
        `tables()`/`expand_all()`/`_select_wcols()` (pure, no I/O), execute via the async
        driver for this backend (the only I/O, on our own connection, not pydal's; see
        `ASYNC_POOL_FACTORIES`), parse via pydal's own `parse()` (pure).
        """
        adapter = self._adapter

        tablenames = adapter.tables(
            query,
            attributes.get("join"),
            attributes.get("left"),
            attributes.get("orderby"),
            attributes.get("groupby"),
        )
        expanded_fields = adapter.expand_all(fields, tablenames)
        colnames, sql = adapter._select_wcols(query, expanded_fields, **attributes)

        pool = await self._get_async_pool()
        try:
            async with pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql)
                rows = await cur.fetchall()
        finally:
            await self._release_readonly_connection(pool)

        limitby = attributes.get("limitby") or (0,)
        rows = adapter.rowslice(rows, limitby[0], None)
        cacheable = attributes.get("cacheable", False)
        return t.cast(pydal.objects.Rows, adapter.parse(rows, expanded_fields, colnames, cacheable=cacheable))

    async def count_async(
        self,
        query: pydal.objects.Query,
        distinct: t.Optional[bool] = None,
    ) -> int:
        """
        Async twin of `db(query).count(distinct)`.

        Mirrors `SQLAdapter.count()` (adapters/base.py): build via pydal's own
        `_count()` (pure), execute via the async driver for this backend, read the first
        column of the first (only) row.
        """
        adapter = self._adapter
        sql = adapter._count(query, distinct)

        pool = await self._get_async_pool()
        try:
            async with pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql)
                row = await cur.fetchone()
        finally:
            await self._release_readonly_connection(pool)

        return t.cast(int, row[0])

    async def update_async(
        self,
        table: pydal.objects.Table,
        query: pydal.objects.Query,
        fields: list[tuple[pydal.objects.Field, t.Any]],
    ) -> t.Optional[int]:
        """
        Async twin of the adapter-level step of `Set.update()`
        (`adapter.update()`, adapters/base.py).

        `fields` is the already-normalized `[(Field, value), ...]` list (`row.op_values()`),
        same shape as `insert_async`'s `fields` - the before_update/after_update hooks and
        validation stay in `QueryBuilder.update_async()`, this is only the execute step.
        """
        adapter = self._adapter
        sql = adapter._update(table, query, fields)

        pool = await self._get_async_pool()
        with self._mark_async_pending():
            async with pool.connection() as conn, conn.cursor() as cur:
                try:
                    await cur.execute(sql)
                except Exception as e:
                    if hasattr(table, "_on_update_error"):
                        return t.cast(t.Optional[int], table._on_update_error(table, query, fields, e))  # ty: ignore[call-non-callable]
                    raise
                try:
                    return cur.rowcount
                except Exception:  # pragma: no cover
                    # defensive, mirroring `adapter.update()` (adapters/base.py):
                    # neither driver's `rowcount` actually raises, it is a plain property.
                    return None

    async def delete_async(
        self,
        table: pydal.objects.Table,
        query: pydal.objects.Query,
    ) -> t.Any:
        """
        Async twin of `Set.delete()`'s adapter-level step (`adapter.delete()`).

        Dispatches per backend via `DELETE_STRATEGIES`: SQLite's isn't a plain
        build/execute/parse call - it selects affected ids first and recurses for
        ON DELETE CASCADE (adapters/sqlite.py) - Postgres's is.
        """
        return await DELETE_STRATEGIES[self._adapter.dbengine](self, table, query)

    async def insert_async(
        self,
        table: pydal.objects.Table,
        fields: list[tuple[pydal.objects.Field, t.Any]],
    ) -> t.Any:
        """
        Async twin of the adapter-level step of `table.insert(**fields)`
        (`adapter.insert()`, adapters/base.py).

        `fields` is the already-normalized `[(Field, value), ...]` list (`row.op_values()`),
        the same shape pydal's own `Table.insert()` passes to the adapter - the field-name-to-
        value normalization, `_before_insert`/`_after_insert` hooks, and validation all stay in
        `TypedTable.insert_async()` (tables.py), not here; this is only the execute step.
        """
        adapter = self._adapter
        query = adapter._insert(table, fields)

        # Capture `_last_insert` here, synchronously, right after the `_insert()` that set it:
        # on Postgres it is a property over `THREAD_LOCAL._pydal_last_insert_` (pydal
        # adapters/postgres.py), and coroutines share one thread, so that thread-local
        # provides no isolation at all on this path. Reading it after the awaits below would
        # read whichever concurrent insert_async() touched it last, not our own.
        last_insert = getattr(adapter, "_last_insert", None)

        pool = await self._get_async_pool()
        with self._mark_async_pending():
            async with pool.connection() as conn, conn.cursor() as cur:
                try:
                    await cur.execute(query)
                except Exception as e:
                    # mirrors `adapter.insert()` (adapters/base.py), same as `update_async`:
                    if hasattr(table, "_on_insert_error"):
                        return table._on_insert_error(table, fields, e)  # ty: ignore[call-non-callable]
                    raise

                if hasattr(table, "_primarykey"):
                    pkdict = {k[0].name: k[1] for k in fields if k[0].name in table._primarykey}  # ty: ignore[unsupported-operator]
                    if pkdict:
                        return pkdict

                row_id = await LASTROWID_STRATEGIES[adapter.dbengine](adapter, table, cur, last_insert)

        # a table with a single custom primarykey reports its id as a `{name: value}` dict
        # instead of a bare int, matching `adapter.insert()` (adapters/base.py):
        primarykey = getattr(table, "_primarykey", None)
        if primarykey is not None and len(primarykey) == 1:  # pragma: no cover
            # unreachable on both supported backends: pydal makes `_primarykey` columns NOT
            # NULL, so an insert omitting the pk fails in the database before the id it would
            # have filled in here could ever be read back. Kept to match `adapter.insert()`
            # (adapters/base.py) for backends that can generate one.
            return {table._primarykey[0]: row_id}  # ty: ignore[not-subscriptable]

        if not isinstance(row_id, int):  # pragma: no cover
            # a driver reporting no lastrowid at all; neither supported backend does.
            return row_id

        reference = pydal.helpers.classes.Reference(row_id)  # ty: ignore[possibly-missing-submodule]
        reference._table = table
        reference._record = None
        return reference

    async def executesql_async(
        self,
        query: str | Template,
        placeholders: t.Iterable[str] | dict[str, str] | None = None,
        as_dict: bool = False,
        fields: "Field | TypedField[t.Any] | Table | t.Iterable[Field | TypedField[t.Any]] | None" = None,
        colnames: t.Iterable[str] | None = None,
        as_ordered_dict: bool = False,
    ) -> list[t.Any] | None:
        """
        Async twin of `executesql(...)`.

        Mirrors pydal's own `DAL.executesql()` (base.py): execute via the async
        driver for this backend (the only I/O), then the same as_dict/fields/colnames
        branching pydal itself does, calling pydal's own `adapter.parse()` (pure) for the
        fields/colnames case, unmodified. Only the plain-tuples path (no as_dict, no
        fields/colnames) is covered by tests so far.
        """
        if SYSTEM_SUPPORTS_TEMPLATES and isinstance(query, Template):  # pragma: no cover
            query = sql_escape_template(self, query)

        adapter = self._adapter
        pool = await self._get_async_pool()

        # unlike the other `_async` methods this one is handed arbitrary SQL, so whether it
        # opens a transaction has to be read off the statement - same test the sync side
        # applies in `SyncTransactionTracker`. A read marks nothing, hence the `nullcontext`.
        opens_a_transaction = str(query).lstrip().upper().startswith(WRITE_STATEMENTS)
        marker = self._mark_async_pending() if opens_a_transaction else contextlib.nullcontext()

        with marker:
            try:
                async with pool.connection() as conn, conn.cursor() as cur:
                    if placeholders:
                        await cur.execute(query, placeholders)
                    else:
                        await cur.execute(query)

                    if as_dict or as_ordered_dict:
                        if not hasattr(cur, "description"):  # pragma: no cover
                            # both supported drivers always expose it; guard kept for parity with
                            # pydal's own `executesql`.
                            raise RuntimeError("database does not support executesql_async(...,as_dict=True)")

                        columns = cur.description
                        result_fields = list(colnames) if colnames else [col[0] for col in columns]
                        if len(result_fields) != len(set(result_fields)):
                            raise RuntimeError(
                                "Result set includes duplicate column names. "
                                "Specify unique column names using the 'colnames' argument",
                            )
                        if columns:
                            for i in range(len(result_fields)):
                                if isinstance(result_fields[i], bytes):  # pragma: no cover
                                    # psycopg and aiosqlite both report column names as str; this is
                                    # for drivers that hand back bytes, as pydal's `executesql` allows.
                                    result_fields[i] = result_fields[i].decode("utf8")  # ty: ignore[unresolved-attribute]

                        data = await cur.fetchall()
                        _dict = collections.OrderedDict if as_ordered_dict else dict
                        return [_dict(zip(result_fields, row)) for row in data]

                    try:
                        data = await cur.fetchall()
                    except Exception:
                        return None
            finally:
                if not opens_a_transaction:
                    await self._release_readonly_connection(pool)

        if fields or colnames:
            if fields is None:
                given_fields: list[t.Any] = []
            elif isinstance(fields, (pydal.objects.Expression, pydal.objects.Table, str, bytes)):
                # pydal's `executesql` accepts one Field/Table instead of a list
                # (base.py: `if not isinstance(fields, list): fields = [fields]`), and the sync
                # `executesql()` above inherits that by delegating to it. Wrapping is not just
                # for parity: `list()` on a single Field never terminates, because
                # `Expression.__getitem__` (pydal objects.py) answers every integer index with a
                # substring expression instead of raising IndexError, so iteration has no end.
                # `str`/`bytes` are in here for the same reason in reverse: neither is valid
                # input, but iterating one silently yields characters, so it would fail much
                # later on `f.sqlsafe` with a character rather than with what was passed in.
                given_fields = [fields]
            else:
                given_fields = list(fields)
            extracted_fields: list[t.Any] = []
            for field in given_fields:
                if isinstance(field, pydal.objects.Table):
                    extracted_fields.extend(list(field))
                else:
                    extracted_fields.append(field)
            if not colnames:
                resolved_colnames = [f.sqlsafe for f in extracted_fields]
            else:
                col_fields = []
                newcolnames = []
                for tf in colnames:
                    if "." in tf:
                        t_f = tf.split(".")
                        tf = ".".join(adapter.dialect.quote(f) for f in t_f)
                    else:
                        t_f = None
                    if not extracted_fields:
                        col_fields.append(t_f)
                    newcolnames.append(tf)
                resolved_colnames = newcolnames
            data = adapter.parse(
                data,
                fields=extracted_fields or [tf and self[tf[0]][tf[1]] for tf in col_fields],
                colnames=resolved_colnames,
            )

        return t.cast(list[t.Any], data)

    async def commit_async(self) -> None:
        """
        Commit the transaction on the async connection.

        Deliberately does not touch `commit()`/the sync connection: queries executed via
        `select_async`/`insert_async`/etc. run on a separate connection, so committing one says
        nothing about the other. On Postgres this ends the transaction on the connection
        checked out for *this task* and returns it to the pool; on SQLite there is one
        connection and it ends the only transaction there is.

        Goes to `AsyncPoolManager.pool` rather than `_get_async_pool()` on purpose: ending a
        transaction must never be the thing that opens a connection, and it must stay callable
        while the sync side has pending writes - the guard in `_get_async_pool()` would refuse
        exactly when a caller is trying to settle up.
        """
        if pool := self._async_pools.pool:
            await pool.commit()

        # only this task's, matching what was actually committed: on Postgres `pool.commit()`
        # ends the transaction on the connection checked out for *this* task and leaves every
        # other task's alone.
        self._async_pending_owners.discard(self._async_pending_owner())

    async def rollback_async(self) -> None:
        """
        Roll back the transaction on the async connection. See `commit_async`.
        """
        if pool := self._async_pools.pool:
            await pool.rollback()

        self._async_pending_owners.discard(self._async_pending_owner())

    async def close_async(self) -> None:
        """
        Close the async connection pool, if one was ever opened.
        """
        await self._async_pools.close()

        # every connection those writes were sitting on is gone (rolled back on the way out),
        # so nothing is pending anymore - and leaving stale owners behind would refuse sync
        # statements on a database that no longer has an async side at all.
        self._async_pending_owners.clear()

    def sql_expression(
        self,
        sql_fragment: str | Template,
        *raw_args: t.Any,
        output_type: str | None = None,
        **raw_kwargs: t.Any,
    ) -> Expression:
        """
        Creates a pydal Expression object representing a raw SQL fragment.

        Args:
            sql_fragment: The raw SQL fragment.
                In python 3.14+, this can also be a t-string. In that case, don't pass other args or kwargs.
            *raw_args: Arguments to be interpolated into the SQL fragment.
            output_type: The expected output type of the expression.
            **raw_kwargs: Keyword arguments to be interpolated into the SQL fragment.

        Returns:
            A pydal Expression object.
        """
        return sql_expression(self, sql_fragment, *raw_args, output_type=output_type, **raw_kwargs)

    def memoize[T: t.Any](
        self,
        func: t.Callable[..., T],
        # should be TypedRows[TypedTable] or TypedTable but for some reason that breaks
        *args: t.Any,
        key: str | None = None,
        ttl: int | dt.timedelta | dt.datetime | None = None,
        # should be P.kwargs but for some reason that breaks
        **kwargs: t.Any,
    ) -> tuple[T, CacheStatus]:
        """
        Cache the result of a function applied to TypedRow(s).

        Tracks dependencies on the table(s) so the cache invalidates
        when those rows are updated/deleted.

        Args:
            func: Function to cache
            *args: Can contain TypedRow, TypedRows, or other args
            key: Cache key (required for lambdas)
            ttl: Time to live in seconds/timedelta, or datetime to expire at
            **kwargs: Passed to func

        Returns:
            Cached result or fresh computation
        """
        return memoize(self, func, *args, key=key, ttl=ttl, **kwargs)  # ty: ignore[invalid-argument-type]

    def as_typescript(self, *tables: str | type[TypedTable]) -> str:
        """
        Generate a TypeScript schema string for all currently defined typedal models.
        """
        TypedDictRegistry.clear()  # clean registry in order to apply 'tables' filtering
        registry = TypedDictRegistry()

        do_filter: t.Callable[[str], bool]
        if tables:
            names = {
                model.__name__
                for table in tables
                if (model := (self.find_model(table) if isinstance(table, str) else table))
            }

            do_filter = lambda name: name in names  # noqa: E731
        else:
            do_filter = lambda name: not name.startswith("_")  # noqa: E731

        # Ensure all currently defined models are registered into the TypedDict registry/world.
        for model in self._class_map.values():
            if do_filter(model.__name__):
                model.as_typeddict()

        return registry.get_typescript("as_typescript")


TypeDAL.representers.setdefault("rows_render", default_representer)

# note: these imports exist at the bottom of this file to prevent circular import issues:

from .fields import *  # noqa: E402 F403 # isort: skip ; to fill globals() scope
from .define import TableDefinitionBuilder  # noqa: E402
from .rows import TypedRows, TypedSet  # noqa: E402
from .tables import TypedTable  # noqa: E402

from .caching import (  # isort: skip # noqa: E402
    memoize,
    _TypedalCache,
    _TypedalCacheDependency,
)
