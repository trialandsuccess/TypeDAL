"""
Contains base functionality related to the Query Builder.
"""

from __future__ import annotations

import copy
import datetime as dt
import functools
import inspect
import math
import time
import typing as t
import warnings
from collections import defaultdict

from pydal.helpers.classes import SQLALL

from .asynchronous import run_async
from .constants import DEFAULT_JOIN_OPTION, JOIN_OPTIONS
from .core import TypeDAL
from .exceptions import AliasedTableMismatchError, ImplicitCrossJoinError
from .fields import TypedField, is_typed_field
from .helpers import (
    DummyQuery,
    all_annotations,
    as_lambda,
    filter_out,
    looks_like,
    normalize_table_keys,
    throw,
)
from .tables import TableMeta, TypedTable, _TypedTable
from .types import (
    CacheMetadata,
    Condition,
    Expression,
    Field,
    Metadata,
    OnQuery,
    OrderBy,
    Permissions,
    Query,
    QueryLike,
    Row,
    Rows,
    Select,
    SelectKwargs,
    T_MetaInstance,
    Table,
    merge_permissions,
    require_permission,
)
from .warnings import NoopQueryWarning, UnusedWindowWarning

_BOOL_OPS = frozenset(("_and", "_or", "_not"))


def _as_join_list(value: Expression | Table | t.Sequence[Expression | Table] | None) -> list[Expression | Table]:
    """Normalize join/left: PyDAL accepts a bare expression as well as a list of them."""
    if value is None:
        return []
    if isinstance(value, (Expression, Table)):
        return [value]
    return list(value)


def _find_table(parents: dict[str, str], name: str) -> str:
    """Union-find lookup of the component a table belongs to."""
    while (parent := parents.setdefault(name, name)) != name:
        name = parent
    return name


def _link_tables(parents: dict[str, str], names: t.Iterable[str]) -> None:
    first, *rest = names
    anchor = _find_table(parents, first)
    for name in rest:
        parents[_find_table(parents, name)] = anchor


def _walk_tables(node: object, tables: set[str], parents: dict[str, str]) -> set[str]:
    """
    Collect the tables referenced by node (like adapter.tables) and link the tables of every comparison.

    Module-level rather than a closure: a self-referencing nested function creates a reference cycle per call,
    and the extra cyclic GC runs made SQL building noticeably slower.
    """
    if isinstance(node, (list, tuple, set)):
        found: set[str] = set()
        for item in node:
            found |= _walk_tables(item, tables, parents)
        return found
    if isinstance(node, Field):
        name = t.cast(str, node.tablename)
        tables.add(name)
        return {name}
    if isinstance(node, SQLALL):
        name = node._table._tablename
        tables.add(name)
        return {name}
    if not isinstance(node, (Expression, Query)):
        return set()
    found = _walk_tables(node.first, tables, parents) | _walk_tables(node.second, tables, parents)
    if len(found) > 1 and isinstance(node, Query) and getattr(node.op, "__name__", None) not in _BOOL_OPS:
        _link_tables(parents, found)
    return found


# (id of the parent record, relationship path, related row id) -> the related instance attached to that parent
type SeenRelations = dict[tuple[int, str, t.Any], t.Any]


class _JoinedTable(t.NamedTuple):
    """A relationship table as it ends up in the SQL of a builder with joins."""

    path: str  # e.g. "users" or "users.bestie"
    relation: Relationship[t.Any]
    table: Table  # aliased when the relationship is joined under an alias
    tablename: str  # the original, unaliased table name
    aliased: bool


def _rewrite_tables(node: t.Any, tables: dict[str, Table]) -> t.Any:
    """
    Rebuild node (a query, expression or field) with every field of a table in `tables` pointing at its alias.

    Unchanged branches are returned as-is, so a query without such fields comes back as the same object.
    """
    if isinstance(node, Field):
        alias = tables.get(t.cast(str, node.tablename))
        return node if alias is None else alias[node.name]
    if isinstance(node, (list, tuple)):
        items = [_rewrite_tables(item, tables) for item in node]
        changed = any(new is not old for new, old in zip(items, node, strict=True))
        return type(node)(items) if changed else node
    if not isinstance(node, (Expression, Query)):
        return node

    first = _rewrite_tables(node.first, tables)
    second = _rewrite_tables(node.second, tables)
    if first is node.first and second is node.second:
        return node

    clone = copy.copy(node)
    clone.first = first
    clone.second = second
    if isinstance(clone, Expression):
        # as Expression.__init__ derives it:
        clone._table = getattr(first, "_table", None)
    return clone


def _requested_tables(part: t.Any) -> list[str]:
    """
    The joined tables a where() lambda asks for: its arguments after the model, e.g. `lambda article, tags: ...`.

    Arguments with a default don't ask for anything.
    """
    if not callable(part) or isinstance(part, (Field, Query, Expression, dict)) or is_typed_field(part):
        return []
    try:
        parameters = inspect.signature(part).parameters.values()
    except (TypeError, ValueError):  # pragma: no cover - builtins without a signature
        return []
    positional = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    names = [p.name for p in parameters if p.kind in positional and p.default is inspect.Parameter.empty]
    return names[1:]


def warn_noop(method: str) -> None:
    """
    Warn that `method` was called without arguments, which has no effect.
    """
    # stacklevel: 1 = here, 2 = the query builder method, 3 = the caller.
    warnings.warn(
        f"`.{method}()` without arguments does nothing. You can remove this call.",
        stacklevel=3,
        category=NoopQueryWarning,
    )


class QueryBuilder[T_MetaInstance: _TypedTable](Select):
    """
    Abstration on top of pydal's query system.
    """

    model: t.Type[T_MetaInstance]
    query: Query
    select_args: list[t.Any]
    select_kwargs: SelectKwargs
    relationships: dict[str, Relationship[t.Any]]
    metadata: Metadata
    _permissions: Permissions
    cross_joins: list[t.Type[TypedTable]]
    # where() calls with a lambda asking for joined tables; resolved at collect time, ANDed with `query`:
    deferred_queries: list[tuple[t.Any, ...]]

    def __setattr__(self, key: str, value: t.Any) -> None:
        """Keep QueryBuilder state independent from Select's table-like storage."""
        object.__setattr__(self, key, value)

    @property
    def _qfields(self) -> list[t.Any]:
        """Expose selected fields for pyDAL's subquery validation."""
        _, select_args, _ = self._before_query({}, add_id=False)
        return select_args

    def _compile(
        self,
        outer_scoped: list[t.Any] | None = None,  # noqa ARG002 - inherit from Select
        with_alias: bool = False,  # noqa ARG002 - inherit from Select
        cte_collector: t.Any = None,  # noqa ARG002 - inherit from Select
    ) -> tuple[list[t.Any], str]:
        """Provide pyDAL's Select protocol without changing QueryBuilder rendering."""
        return [], self.to_sql()

    def __init__(
        self,
        model: t.Type[T_MetaInstance],
        add_query: Query | None = None,
        select_args: list[t.Any] | None = None,
        select_kwargs: SelectKwargs | None = None,
        relationships: dict[str, Relationship[t.Any]] | None = None,
        metadata: Metadata | None = None,
        permissions: Permissions | None = None,
        cross_joins: list[t.Type[TypedTable]] | None = None,
        deferred_queries: list[tuple[t.Any, ...]] | None = None,
    ):
        """
        Normally, you wouldn't manually initialize a QueryBuilder but start using a method on a TypedTable.

        Example:
            MyTable.where(...) -> QueryBuilder[MyTable]
        """
        self.model = model
        table = self._ensure_table_defined()
        default_query: Query = table.id > 0
        self.query = add_query or default_query
        self.select_args = select_args or []
        self.select_kwargs = select_kwargs or {}
        self.relationships = relationships or {}
        self.metadata = metadata or {}
        self._permissions = merge_permissions(getattr(model, "_permissions", None), permissions)
        self.cross_joins = cross_joins or []
        self.deferred_queries = deferred_queries or []

    def _ensure_table_defined(self) -> Table:
        model = self.model
        if hasattr(model, "_ensure_table_defined"):
            return model._ensure_table_defined()
        else:
            # already a pydal table
            return t.cast(Table, model)

    def __str__(self) -> str:
        """
        Simple string representation for the query builder.
        """
        return f"QueryBuilder for {self.model}"

    def __repr__(self) -> str:
        """
        Advanced string representation for the query builder.
        """
        return (
            f"<QueryBuilder for {self.model} with "
            f"{len(self.select_args)} select args; "
            f"{len(self.select_kwargs)} select kwargs; "
            f"{len(self.relationships)} relationships; "
            f"query:    {bool(self.query)}; "
            f"metadata: {self.metadata}; "
            f">"
        )

    def __bool__(self) -> bool:
        """
        Querybuilder is truthy if it has t.Any conditions.
        """
        table = self._ensure_table_defined()
        default_query: Query = table.id > 0
        return any(
            [
                self.query != default_query,
                self.select_args,
                self.select_kwargs,
                self.relationships,
                self.metadata,
                self.cross_joins,
                self.deferred_queries,
            ],
        )

    def _extend(
        self,
        add_query: Query | None = None,
        overwrite_query: Query | None = None,
        select_args: list[t.Any] | None = None,
        select_kwargs: SelectKwargs | None = None,
        relationships: dict[str, Relationship[t.Any]] | None = None,
        metadata: Metadata | None = None,
        permissions: Permissions | None = None,
        cross_joins: list[t.Type[TypedTable]] | None = None,
        deferred_queries: list[tuple[t.Any, ...]] | None = None,
    ) -> "QueryBuilder[T_MetaInstance]":
        return QueryBuilder(
            self.model,
            (add_query & self.query) if add_query else overwrite_query or self.query,
            (self.select_args + select_args) if select_args else self.select_args,
            (self.select_kwargs | select_kwargs) if select_kwargs else self.select_kwargs,
            (self.relationships | relationships) if relationships else self.relationships,
            (self.metadata | (metadata or {})) if metadata else self.metadata,  # ty: ignore[invalid-argument-type]
            permissions=merge_permissions(self._permissions, permissions),
            cross_joins=self.cross_joins + (cross_joins or []),
            deferred_queries=self.deferred_queries + (deferred_queries or []),
        )

    def permissions(self, **permissions: t.Unpack[Permissions]) -> "QueryBuilder[T_MetaInstance]":
        """
        Return a clone of this builder with permission overrides merged in.
        """
        if not permissions and self:  # ty: ignore[redundant-condition]
            warn_noop("permissions")
            return self

        return self._extend(permissions=permissions)

    def _normalize_select_option(
        self, value: str | Field | Expression | bool | t.Iterable[str | Field]
    ) -> str | bool | list[str]:
        # currently only used for 'distinct' since orderby, ... are patched by pydal itself in select()
        if isinstance(value, bool):
            return value

        if isinstance(value, (list, tuple, set)):
            return t.cast(list[str], [self._normalize_select_option(val) for val in value])

        if rname := getattr(value, "_rname", None):
            return str(rname)

        return str(value)

    def select(self, *fields: t.Any, **options: t.Unpack[SelectKwargs]) -> "QueryBuilder[T_MetaInstance]":
        """
        Fields: database columns by name ('id'), by field reference (table.id) or other (e.g. table.ALL).

        Options:
            paraphrased from the web2py pydal docs,
            For more info, see http://www.web2py.com/books/default/chapter/29/06/the-database-abstraction-layer#orderby-groupby-limitby-distinct-having-orderby_on_limitby-join-left-cache

            orderby: field(s) to order by. Supported:
                table.name - sort by name, ascending
                ~table.name - sort by name, descending
                <random> - sort randomly
                table.name|table.id - sort by two fields (first name, then id)

            groupby, having: together with orderby:
                groupby can be a field (e.g. table.name) to group records by
                having can be a query, only those `having` the condition are grouped

            limitby: tuple of min and max. When using the query builder, .paginate(limit, page) is recommended.
            distinct: bool/field. Only select rows that differ
            orderby_on_limitby (bool, default: True): by default, an implicit orderby is added when doing limitby.
            join: othertable.on(query) - do an INNER JOIN. Using TypeDAL relationships with .join() is recommended!
            left: othertable.on(query) - do a LEFT JOIN. Using TypeDAL relationships with .join() is recommended!
            cache: cache the query result to speed up repeated queries; e.g. (cache=(cache.ram, 3600), cacheable=True)

        Calling this without any fields or options on a builder that already has settings
        does nothing and emits a NoopQueryWarning.
        """
        if not fields and not options and self:
            warn_noop("select")
            return self

        for key in ("distinct",):
            if options.get(key):
                options[key] = self._normalize_select_option(options[key])

        return self._extend(select_args=list(fields), select_kwargs=options)

    def orderby(self, *fields: OrderBy) -> "QueryBuilder[T_MetaInstance]":
        """
        Order the query results by specified fields.

        Args:
            fields: field(s) to order by. Supported:
                table.name - sort by name, ascending
                ~table.name - sort by name, descending
                <random> - sort randomly
                table.name|table.id - sort by two fields (first name, then id)

        Returns:
            QueryBuilder: A new QueryBuilder instance with the ordering applied.
        """
        return self.select(orderby=fields)

    def groupby(self, *fields: t.Any) -> "QueryBuilder[T_MetaInstance]":
        """
        Group the query results by specified fields.

        Args:
            fields: Field(s) to group by (e.g., Table.column)

        Returns:
            QueryBuilder: A new QueryBuilder instance with grouping applied.
        """
        groupby_value = fields[0] if len(fields) == 1 else fields
        return self.select(groupby=groupby_value)

    def having(self, condition: t.Any) -> "QueryBuilder[T_MetaInstance]":
        """
        Filter grouped query results based on aggregate conditions.

        Args:
            condition: Query condition for filtering groups (e.g., Team.id.count() > 0)

        Returns:
            QueryBuilder: A new QueryBuilder instance with having condition applied.
        """
        return self.select(having=condition)

    def where(
        self,
        *queries_or_lambdas: Query | t.Callable[..., Query] | dict[str, t.Any],
        **filters: t.Any,
    ) -> "QueryBuilder[T_MetaInstance]":
        """
        Extend the builder's query.

        Can be used in multiple ways:
        .where(Query) -> with a direct query such as `Table.id == 5`
        .where(lambda table: table.id == 5) -> with a query via a lambda
        .where(id=5) -> via keyword arguments

        When using multiple where's, they will be ANDed:
            .where(lambda table: table.id == 5).where(lambda table: table.id == 6) == (table.id == 5) & (table.id=6)
        When passing multiple queries to a single .where, they will be ORed:
            .where(lambda table: table.id == 5, lambda table: table.id == 6) == (table.id == 5) | (table.id=6)

        Joined relationships live under an alias in the SQL. A field of a joined table (`.where(Tag.name == "x")`)
        is resolved to that alias when the builder runs, so `.where(...).join(...)` and `.join(...).where(...)` both
        work. When the same table is joined more than once, pick the join with extra lambda arguments, named after
        the relationship (nested ones as `parent__child`), which are also resolved when the builder runs:
            .join("tags").where(lambda article, tags: tags.name == "x")

        Calling this without any arguments on a builder that already has settings
        does nothing and emits a NoopQueryWarning.
        Starting an empty builder (e.g. `Model.where()`) is allowed and stays silent.
        """
        if not queries_or_lambdas and not filters and self:
            warn_noop("where")
            return self

        queries_or_lambdas = (
            *queries_or_lambdas,
            filters,
        )

        if any(_requested_tables(query_part) for query_part in queries_or_lambdas):
            # the joined tables (and their aliases) are only known once the builder runs:
            return self._extend(deferred_queries=[queries_or_lambdas])

        new_query = self.query
        if subquery := self._where_group(queries_or_lambdas, {}):
            new_query &= subquery

        return self._extend(overwrite_query=new_query)

    def _where_group(self, queries_or_lambdas: tuple[t.Any, ...], joined: dict[str, Table]) -> Query:
        """
        OR the parts of one where() call together.

        `joined` maps relationship names to their (aliased) tables, for lambdas that ask for them by name.
        """
        table = self._ensure_table_defined()
        subquery = t.cast(Query, DummyQuery())
        for query_part in queries_or_lambdas:
            if isinstance(query_part, Field) or is_typed_field(query_part):
                subquery |= query_part != None
            elif isinstance(query_part, (Query, Expression)):
                subquery |= t.cast(Query, query_part)
            elif callable(query_part):
                if result := query_part(self.model, *self._bind_joined_tables(query_part, joined)):
                    subquery |= t.cast(Query, result)
            elif isinstance(query_part, dict):
                subsubquery = DummyQuery()
                for field, value in query_part.items():
                    subsubquery &= table[field] == value
                if subsubquery:
                    # DummyQuery is falsy, so reaching here means it became a Query.
                    subquery |= t.cast(Query, subsubquery)
            else:
                raise ValueError(f"Unexpected query type ({type(query_part)}).")

        return subquery

    def _bind_joined_tables(self, fn: t.Callable[..., t.Any], joined: dict[str, Table]) -> list[Table]:
        """Look up the joined tables that a where() lambda asks for, by the names of its extra arguments."""
        names = _requested_tables(fn)
        if missing := [name for name in names if name not in joined]:
            available = ", ".join(sorted(joined)) or "none"
            raise ValueError(f"where() asks for unjoined relationship(s) {', '.join(missing)} (joined: {available})")
        return [joined[name] for name in names]

    def _parse_relationships(
        self,
        fields: t.Iterable[str | t.Type[TypedTable]],
        method: JOIN_OPTIONS = None,
        **update: t.Any,
    ) -> dict[str, Relationship[t.Any]]:
        """
        Parse relationship fields into a dict of base relationships with nested relationships.

        Args:
            fields: Iterable of relationship field names
                (e.g., ['relationship', 'relationship.with_nested', 'relationship.no2'])
            method: Join method to use instead of each relationship's own
            update: Extra settings passed to the relationship clones (e.g. condition_and)

        Returns:
            Dict mapping base relationship names to Relationship objects with nested relationships
            Example: {'relationship': Relationship('relationship',
                                                        nested={'with_nested': Relationship(),
                                                                'no2': Relationship()})}
        """
        relationships: dict[str, Relationship[t.Any]] = {}
        base_relationships = self.model.get_relationships()
        db = self._get_db()

        for field in fields:
            relation_name = str(field)
            parts = relation_name.split(".")
            base_name = parts[0]

            # Create base relationship if it doesn't exist
            if base_name not in relationships:
                relationships[base_name] = base_relationships[base_name].clone(join=method, **update)

            # If this is a nested relationship, traverse and add it
            if len(parts) > 1:
                current = relationships[base_name]

                for level in parts[1:]:
                    # Check if this nested relationship already exists
                    if level not in current.nested:
                        # Create new nested relationship
                        subrelationship = current.get_table(db).get_relationships()[level].clone(join=method)
                        current.nested[level] = subrelationship

                    current = current.nested[level]

        return relationships

    def join(
        self,
        *fields: str | t.Type[TypedTable] | Relationship[t.Any],
        method: JOIN_OPTIONS = None,
        on: OnQuery | list[Expression] | Expression = None,
        condition: Condition = None,
        condition_and: Condition = None,
    ) -> "QueryBuilder[T_MetaInstance]":
        """
        Include relationship fields in the result.

        Supports:
          - join("example")
          - join(Table.example, "second", method="left")
          - join(Table.example, on=...)
          - join(Table.example, condition=...)

        `fields` can be names or Relationship instances.
        If no fields are passed, all relationships will be joined.

        `fields` can be names of Relationships on the current model.
        If no fields are passed, all will be used.

        By default, the `method` defined in the relationship is used.
            This can be overwritten with the `method` keyword argument (left or inner)

        `condition_and` can be used to add extra conditions to an inner join.
        """
        # todo: allow limiting amount of related rows returned for join?
        # todo: it would be nice if 'fields' could be an actual relationship
        #   (Article.tags = list[Tag]) and you could change the .condition and .on
        #  this could deprecate condition_and

        if condition and on:
            raise Relationship._error_duplicate_condition(condition, on)  # type: ignore

        relationships: dict[str, Relationship[t.Any]]

        if condition:
            if len(fields) != 1:
                raise ValueError("join(field, condition=...) can only be used with exactly one field!")

            if isinstance(condition, Query):
                condition = as_lambda(condition)

            field = fields[0]
            if isinstance(field, str):
                field = self.model.get_relationships().get(field, field)
            if isinstance(field, Relationship) and field.name:
                relationships = {
                    field.name: field.clone(condition=condition, on=None, join=method, condition_and=condition_and)
                }
            else:
                to_field = t.cast(t.Type[TypedTable], field)
                relationships = {
                    str(to_field): Relationship(to_field, condition=condition, join=method, condition_and=condition_and)
                }
        elif on:
            if len(fields) != 1:
                raise ValueError("join(field, on=...) can only be used with exactly one field!")

            if isinstance(on, Expression):
                on = [on]

            if isinstance(on, list):
                on = t.cast(OnQuery, as_lambda(on))

            field = fields[0]
            if isinstance(field, Relationship) and field.name:
                relationships = {
                    field.name: field.clone(on=on, join=method, condition=None, condition_and=condition_and)
                }
            else:
                to_field = t.cast(t.Type[TypedTable], field)
                relationships = {str(to_field): Relationship(to_field, on=on, join=method, condition_and=condition_and)}
        elif fields:
            # join on every relationship
            # simple: 'relationship'
            #   -> {'relationship': Relationship('relationship')}
            # complex with one: relationship.with_nested
            #   -> {'relationship': Relationship('relationship', nested=[Relationship('with_nested')])
            # complex with two:  relationship.with_nested,  relationship.no2
            #   -> {'relationship': Relationship('relationship',
            #                           nested=[Relationship('with_nested'), Relationship('no2')])

            # fields is a tuple so that's not mutable, filter_out requires a mutable dict:
            other_fields = {idx: field for idx, field in enumerate(fields)}
            relationship_instances = filter_out(other_fields, Relationship)

            relationships = {}

            # Clone direct Relationship instances (preserving their settings)
            for relationship in relationship_instances.values():
                if relationship.name:  # pragma: no branch - bound relationships always have a name
                    relationships[relationship.name] = relationship.clone(
                        join=method,
                        condition_and=condition_and,
                    )

            # Parse and merge string/table fields
            if other_fields:
                parsed_relationships = self._parse_relationships(
                    other_fields.values(),  # type: ignore
                    method=method,
                    condition_and=condition_and,
                )
                # Explicit Relationship instances take precedence
                relationships = parsed_relationships | relationships

        else:
            relationships = {k: v for k, v in self.model.get_relationships().items() if not v.explicit}

        return self._extend(relationships=relationships)

    def cross_join(self, table: t.Type[TypedTable]) -> "QueryBuilder[T_MetaInstance]":
        """Explicitly include an unrelated table in the query."""
        if table._db is not self._get_db():
            raise ValueError("cross join table must belong to the same database")
        if table in self.cross_joins:
            return self
        return self._extend(cross_joins=[table])

    def cache(
        self,
        *deps: t.Any,
        expires_at: t.Optional[dt.datetime] = None,
        ttl: t.Optional[int | dt.timedelta] = None,
    ) -> "QueryBuilder[T_MetaInstance]":
        """
        Enable caching for this query.

        Repeated calls are loaded from a dill row instead of executing the sql and collecting matching rows again.
        """
        existing = self.metadata.get("cache", {})

        metadata: Metadata = {}

        cache_meta = t.cast(
            CacheMetadata,
            self.metadata.get("cache", {})
            | {
                "enabled": True,
                "depends_on": existing.get("depends_on", []) + [str(_) for _ in deps],
                "expires_at": get_expire(expires_at=expires_at, ttl=ttl),
            },
        )

        metadata["cache"] = cache_meta
        return self._extend(metadata=metadata)

    def _get_db(self) -> TypeDAL:
        return self.model._db or throw(EnvironmentError("@define or db.define is not called on this class yet!"))

    def _select_arg_convert(self, arg: t.Any) -> t.Any:
        # typedfield are not really used at runtime t.Anymore, but leave it in for safety:
        if isinstance(arg, TypedField):  # pragma: no cover
            arg = arg._field

        return arg

    def _mutation_query(self) -> Query:
        """
        The query for delete and update, which ignore joins.

        A where() lambda that asks for joined tables only filters through those joins, so leaving it out would
        delete or update more rows than asked for.
        """
        if self.deferred_queries:
            raise ValueError("delete() and update() can't use where() lambdas that ask for joined relationships")
        return self.query

    def delete(self) -> list[int]:
        """
        Based on the current query, delete rows and return a list of deleted IDs.
        """
        require_permission(self._permissions, "delete")
        db = self._get_db()
        removed_ids = [_.id for _ in db(self._mutation_query()).select("id")]
        if db(self._mutation_query()).delete():
            # success!
            return removed_ids

        return []

    def _delete(self) -> str:
        db = self._get_db()
        return str(db(self._mutation_query())._delete())

    # QueryBuilder subclasses Select for its query-building surface but yields
    # model instances rather than fields, so these two are not substitutable.
    def update(self, **fields: t.Any) -> list[int]:  # ty: ignore[invalid-method-override]
        """
        Based on the current query, update `fields` and return a list of updated IDs.
        """
        # todo: limit?
        require_permission(self._permissions, "update")
        db = self._get_db()
        from .updates import UpdateSet

        # a TypedTable's primary key is always its integer id
        return t.cast(list[int], t.cast(UpdateSet, db(self._mutation_query())).update_ids(**fields))

    def _update(self, **fields: t.Any) -> str:
        db = self._get_db()
        return str(db(self._mutation_query())._update(**fields))

    def _joined_tables(self) -> list[_JoinedTable]:
        """Every joined relationship (nested ones included) with the table it uses in the SQL."""
        joined: list[_JoinedTable] = []
        for key, relation in self.relationships.items():
            self._collect_joined_tables(relation, key, self.model, key, joined)
        return joined

    def _collect_joined_tables(
        self,
        relation: Relationship[t.Any],
        key: str,
        parent: t.Any,
        path: str,
        joined: list[_JoinedTable],
    ) -> None:
        # mirrors the aliasing in _process_relationship_for_left_join and _build_inner_joins_recursive:
        # everything is aliased, except an `on` relationship to another table than its parent or the root.
        other = relation.get_table(self._get_db())
        aliased = other is parent or other is self.model or not relation.on
        table = other.with_alias(f"{key}_{hash(relation)}") if aliased else other
        joined.append(_JoinedTable(path, relation, t.cast(Table, table), other._tablename, aliased))

        for nested_name, nested in relation.nested.items():
            self._collect_joined_tables(nested, nested_name, table, f"{path}.{nested_name}", joined)

    def _alias_rewrites(self, joined: list[_JoinedTable]) -> dict[str, Table]:
        """
        Map table names to the one alias they are joined under.

        Left out (so the table name keeps meaning the table itself): the root table, `cross_join()` tables,
        tables joined without an alias (`on=`) and tables joined more than once, which _validate_joins rejects.
        """
        excluded = {self._ensure_table_defined()._tablename}
        excluded |= {table._ensure_table_defined()._tablename for table in self.cross_joins}

        per_table: dict[str, list[_JoinedTable]] = defaultdict(list)
        for entry in joined:
            per_table[entry.tablename].append(entry)

        return {
            name: entries[0].table
            for name, entries in per_table.items()
            if len(entries) == 1 and entries[0].aliased and name not in excluded
        }

    @staticmethod
    def _joined_by_name(joined: list[_JoinedTable]) -> dict[str, Table]:
        """The names a where() lambda can ask for: `tags`, `tags__author` and, when unambiguous, `author`."""
        names = {entry.path.replace(".", "__"): entry.table for entry in joined}

        per_leaf: dict[str, list[Table]] = defaultdict(list)
        for entry in joined:
            per_leaf[entry.path.rsplit(".", 1)[-1]].append(entry.table)
        for name, tables in per_leaf.items():
            if len(tables) == 1:
                names.setdefault(name, tables[0])

        return names

    def _resolve_query(self, joined: list[_JoinedTable], rewrites: dict[str, Table]) -> Query:
        """The builder's query with the deferred where() lambdas applied and joined tables pointing at their alias."""
        query = self.query
        if self.deferred_queries:
            by_name = self._joined_by_name(joined)
            for queries_or_lambdas in self.deferred_queries:
                if subquery := self._where_group(queries_or_lambdas, by_name):
                    query &= subquery

        return t.cast(Query, _rewrite_tables(query, rewrites)) if rewrites else query

    @staticmethod
    def _required_join_names(joined: list[_JoinedTable], *nodes: t.Any) -> set[str]:
        """
        The (aliased) names of the joined tables that `nodes` (a predicate, a counted field) use.

        Includes the joins they hang from, since a nested join's ON clause references its parent's join.
        """
        referenced: set[str] = set()
        for node in nodes:
            referenced |= _walk_tables(node, set(), {})
        paths = {entry.path for entry in joined if entry.table._tablename in referenced}
        paths |= {path.rsplit(".", depth)[0] for path in paths for depth in range(1, path.count(".") + 1)}
        return {entry.table._tablename for entry in joined if entry.path in paths}

    @staticmethod
    def _required_left_joins(names: set[str], left_joins: list[Expression]) -> list[Expression]:
        """
        The left joins of the tables in `names`, see _required_join_names.

        Queries that replace the select (the id subquery of limitby, count) need these: without them, a filter on a
        left-joined table references a table that isn't in their FROM clause.
        """
        return [join for join in left_joins if isinstance(join.first, Table) and join.first._tablename in names]

    def _before_query(self, mut_metadata: Metadata, add_id: bool = True) -> tuple[Query, list[t.Any], SelectKwargs]:
        select_args = [self._select_arg_convert(_) for _ in self.select_args] or [self.model.ALL]
        select_kwargs = self.select_kwargs.copy()
        joined = self._joined_tables()
        rewrites = self._alias_rewrites(joined)
        query = self._resolve_query(joined, rewrites)
        for option in ("orderby", "groupby", "having"):
            if rewrites and option in select_kwargs:
                select_kwargs[option] = _rewrite_tables(select_kwargs[option], rewrites)
        model = self.model
        mut_metadata["query"] = query
        # require at least id of main table:
        select_fields = ", ".join([str(_) for _ in select_args])
        tablename = str(model)

        if add_id and f"{tablename}.id" not in select_fields:
            # fields of other selected, but required ID is missing.
            select_args.append(model.id)

        if self.relationships:
            query, select_args = self._handle_relationships_pre_select(
                query, select_args, select_kwargs, mut_metadata, joined
            )
        else:
            self._validate_joins(query, [], select_args, select_kwargs, joined)

        for table in self.cross_joins:
            query &= table.id > 0

        return query, select_args, select_kwargs

    def _validate_joins(
        self,
        query: Query,
        conditions: list[QueryLike],
        fields: list[t.Any],
        options: SelectKwargs,
        joined: list[_JoinedTable],
    ) -> None:
        """
        Reject tables that would end up in the FROM clause without anything relating them to the root table.

        Walks the (resolved) predicate, the extra `conditions`, the selected `fields` and the ON clauses once.
        Every table referenced inside a single comparison (`A.x == B.y`, but also `(A.x + B.y) > 3`) counts as
        linked. The root table, explicit JOIN targets and `cross_join()` tables seed the connected component.
        """
        root = self._ensure_table_defined()._tablename
        explicit = {table._ensure_table_defined()._tablename for table in self.cross_joins}
        joins = [*_as_join_list(options.get("join")), *_as_join_list(options.get("left"))]

        tables: set[str] = set()
        parents: dict[str, str] = {}
        walk = functools.partial(_walk_tables, tables=tables, parents=parents)

        predicate_tables = walk(query)
        for condition in conditions:
            walk(condition)
        for field in fields:
            walk(field)

        seeds = {root, *explicit}
        for join in joins:
            if isinstance(join, Table):
                seeds.add(join._tablename)
                continue
            if isinstance(join.first, Table):  # pragma: no branch - `table.on(...)` always starts with a table
                seeds.add(join.first._tablename)
            walk(join.second)

        # _alias_rewrites resolved every table joined under exactly one alias, what's left is ambiguous:
        per_table: dict[str, list[_JoinedTable]] = defaultdict(list)
        for entry in joined:
            per_table[entry.tablename].append(entry)
        for original, entries in per_table.items():
            if original not in predicate_tables or original == root or original in explicit:
                continue
            if all(entry.aliased for entry in entries):
                paths = ", ".join(repr(entry.path) for entry in entries)
                pick = entries[0].path.replace(".", "__")
                raise AliasedTableMismatchError(
                    f"Table {original!r} is joined under multiple aliases ({paths}); "
                    f"pick one with .where(lambda row, {pick}: ...)"
                )

        tables |= seeds
        _link_tables(parents, seeds)
        anchor = _find_table(parents, root)
        if disconnected := {name for name in tables if _find_table(parents, name) != anchor}:
            names = ", ".join(sorted(disconnected))
            raise ImplicitCrossJoinError(f"Implicit cross join involving {names}; use cross_join()")

    def to_sql(self, add_id: bool = False) -> str:
        """
        Generate the SQL for the built query.
        """
        db = self._get_db()

        query, select_args, select_kwargs = self._before_query({}, add_id=add_id)

        return str(db(query)._select(*select_args, **select_kwargs))

    def _collect(self) -> str:
        """
        Alias for to_sql, pydal-like syntax.
        """
        return self.to_sql()

    def _collect_cached(
        self,
        metadata: Metadata,
        into: t.Type[_TypedTable],
    ) -> "TypedRows[T_MetaInstance] | None":
        expires_at = metadata["cache"].get("expires_at")
        metadata["cache"] |= {
            # key is partly dependant on cache metadata but not these:
            "key": None,
            "status": None,
            "cached_at": None,
            "expires_at": None,
        }

        _, key = create_and_hash_cache_key(
            self.model,
            f"{into.__module__}.{into.__qualname__}",
            metadata,
            self._cache_key_query(),
            self.select_args,
            self.select_kwargs,
            self.relationships.keys(),
        )

        # re-set after creating key:
        metadata["cache"]["expires_at"] = expires_at
        metadata["cache"]["key"] = key

        return load_from_cache(key, self._get_db())

    def _cache_key_query(self) -> Query | str:
        """The resolved query, with alias names (which contain a per-process hash) replaced by relationship paths."""
        joined = self._joined_tables()
        query = self._resolve_query(joined, self._alias_rewrites(joined))
        if query is self.query:
            return query

        key = str(query)
        for entry in joined:
            if entry.aliased:
                key = key.replace(entry.table._tablename, f"<{entry.path}>")
        return key

    def execute(self, add_id: bool = False) -> Rows:
        """
        Raw version of .collect which only executes the SQL, without performing t.Any magic afterwards.
        """
        require_permission(self._permissions, "read")
        db = self._get_db()
        metadata: Metadata = self.metadata.copy()

        query, select_args, select_kwargs = self._before_query(metadata, add_id=add_id)

        for fn_before in db._before_execute:
            fn_before(self)

        rows: Rows = db(query).select(*select_args, **select_kwargs)

        for fn_after in db._after_execute:
            fn_after(self, rows)

        return rows

    def collect(
        self,
        verbose: bool = False,
        _to: t.Type["TypedRows[t.Any]"] | None = None,
        add_id: bool = True,
        _into: t.Type[_TypedTable] | None = None,
        _init: t.Callable[[_TypedTable, Row], None] | None = None,
    ) -> TypedRows[T_MetaInstance]:
        """
        Execute the built query and turn it into model instances, while handling relationships.
        """
        require_permission(self._permissions, "read")
        if _to is None:
            _to = TypedRows
        into = _into or self.model

        if not isinstance(self.model, TableMeta):
            # tried to use querybuilder with a non-typedal table,
            # fallback to execute:
            return t.cast(TypedRows[T_MetaInstance], self.execute(add_id=add_id))

        db = self._get_db()

        for fn_before in db._before_collect:
            fn_before(self)

        metadata: Metadata = self.metadata.copy()

        if metadata.get("cache", {}).get("enabled") and (result := self._collect_cached(metadata, into)):
            return result

        query, select_args, select_kwargs = self._before_query(metadata, add_id=add_id)

        window_size = metadata.get("window_size", None)
        is_iterating = metadata.get("iterating", False)

        if window_size is not None and is_iterating is not True:
            warnings.warn(
                "A window size was configured, but collect() was called directly. "
                "Use iteration over the query builder to fetch rows in windows.",
                stacklevel=2,
                category=UnusedWindowWarning,
            )

        metadata["sql"] = db(query)._select(*select_args, **select_kwargs)

        if verbose:  # pragma: no cover
            print(metadata["sql"])

        start_time = time.perf_counter()
        rows: Rows = db(query).select(*select_args, **select_kwargs)
        duration = time.perf_counter() - start_time

        metadata["final_query"] = str(query)
        metadata["final_args"] = [str(_) for _ in select_args]
        metadata["final_kwargs"] = select_kwargs
        metadata["select_duration"] = duration

        if verbose:  # pragma: no cover
            print(rows)

        if not self.relationships:
            # easy
            typed_rows = _to.from_rows(rows, self.model, metadata=metadata, into=into, init=_init)
        else:
            # harder: try to match rows to the belonging objects
            # assume structure of {'table': <data>} per row.
            # if that's not the case, return default behavior again
            typed_rows = self._collect_with_relationships(rows, metadata=metadata, _to=_to, _into=into, _init=_init)

        for fn_after in db._after_collect:
            fn_after(self, typed_rows, rows)

        # only saves if requested in metadata:
        return save_to_cache(typed_rows, rows)

    def collect_into[T_Into: _TypedTable](
        self,
        into: t.Type[T_Into],
        verbose: bool = False,
        add_id: bool = True,
        init: t.Callable[[T_Into, Row], None] | None = None,
    ) -> TypedRows[T_Into]:
        """
        Execute the built query and instantiate root records as another model class.
        """
        self._validate_collect_into_model(into)
        query = self
        if not self.select_args:
            query = self.select(*self._collect_into_default_fields(into))
        _init = t.cast(t.Callable[[_TypedTable, Row], None] | None, init)
        rows = query.collect(verbose=verbose, add_id=add_id, _into=into, _init=_init)
        return t.cast(TypedRows[T_Into], rows)

    def _validate_collect_into_model(self, into: t.Type[t.Any]) -> None:
        if not isinstance(into, TableMeta):
            raise TypeError("collect_into expects a TypedTable class")

        source = self.model._ensure_table_defined()

        if not getattr(into, "_table", None):
            into.__set_internals__(db=self._get_db(), table=source, relationships={})

        target = into._ensure_table_defined()

        if source is target or str(source) == str(target):
            return

        raise ValueError(
            f"collect_into target '{into.__name__}' must be bound to table '{source}', got '{target}'",
        )

    def _collect_into_default_fields(self, into: t.Type[_TypedTable]) -> list[t.Any]:
        source = self.model._ensure_table_defined()

        return [source[name] for name in all_annotations(into) if name in source.fields]

    @t.overload
    def column[T: t.Any](self, field: TypedField[T], **options: t.Unpack[SelectKwargs]) -> list[T]:
        """
        If a typedfield is passed, the output type can be safely determined.
        """

    @t.overload
    def column[T: t.Any](self, field: T, **options: t.Unpack[SelectKwargs]) -> list[T]:
        """
        Otherwise, the output type is loosely determined (assumes `field: type` or t.Any).
        """

    def column[T: t.Any](self, field: TypedField[T] | T, **options: t.Unpack[SelectKwargs]) -> list[T]:
        """
        Get all values in a specific column.

        Shortcut for `.select(field).execute().column(field)`.
        """
        return self.select(field, **options).execute().column(field)

    def _handle_relationships_pre_select(
        self,
        query: Query,
        select_args: list[t.Any],
        select_kwargs: SelectKwargs,
        metadata: Metadata,
        joined: list[_JoinedTable],
    ) -> tuple[Query, list[t.Any]]:
        """Handle relationship joins and field selection for database query."""
        # Collect all relationship keys including nested ones
        metadata["relationships"] = self._collect_all_relationship_keys()

        # Build joins and handle field selection
        inner_joins = self._build_inner_joins()
        left_joins: list[Expression] = []
        select_args = self._build_left_joins_and_fields(select_args, left_joins)

        # validate before the limitby optimization replaces the predicate with an ID subquery:
        self._validate_joins(
            query,
            [],
            select_args,
            {"join": inner_joins or select_kwargs.get("join"), "left": left_joins},
            joined,
        )
        required_names = self._required_join_names(
            joined, query, select_kwargs.get("orderby"), select_kwargs.get("groupby"), select_kwargs.get("having")
        )
        required_left = self._required_left_joins(required_names, left_joins)
        query = self._apply_limitby_optimization(query, select_kwargs, inner_joins, metadata, required_left)

        if inner_joins:
            select_kwargs["join"] = inner_joins

        select_kwargs["left"] = left_joins
        return query, select_args

    def _collect_all_relationship_keys(self) -> set[str]:
        """Collect all relationship keys including nested ones."""
        keys = set(self.relationships.keys())

        for relation in self.relationships.values():
            keys.update(self._collect_nested_keys(relation))

        return keys

    def _collect_nested_keys(self, relation: Relationship[t.Any], prefix: str = "") -> set[str]:
        """Recursively collect nested relationship keys."""
        keys = set()

        for name, nested in relation.nested.items():
            nested_key = f"{prefix}.{name}" if prefix else name
            keys.add(nested_key)
            keys.update(self._collect_nested_keys(nested, nested_key))

        return keys

    def _build_inner_joins(self) -> list[t.Any]:
        """Build inner joins for relationships with conditions."""
        joins = []

        for key, relation in self.relationships.items():
            joins.extend(self._build_inner_joins_recursive(relation, self.model, key))

        return joins

    def _build_inner_joins_recursive(
        self,
        relation: Relationship[t.Any],
        parent_table: t.Type[_TypedTable],
        key: str,
        parent_key: str = "",
    ) -> list[t.Any]:
        """Recursively build inner joins for a relationship and its nested relationships."""
        db = self._get_db()
        joins = []

        # Handle current level
        if relation.condition and relation.join == "inner":
            other = relation.get_table(db)
            other = other.with_alias(f"{key}_{hash(relation)}")
            condition = relation.condition(parent_table, other)  # ty: ignore[invalid-argument-type]

            if callable(relation.condition_and):
                condition &= relation.condition_and(parent_table, other)  # ty: ignore[invalid-argument-type]

            joins.append(other.on(condition))

            # Process nested relationships
            for nested_name, nested in relation.nested.items():
                # todo: add additional test, deduplicate
                nested_key = f"{parent_key}.{nested_name}" if parent_key else f"{key}.{nested_name}"
                joins.extend(self._build_inner_joins_recursive(nested, other, nested_name, nested_key))

        return joins

    def _selectable_orderby_fields(self, orderby: OrderBy | t.Iterable[OrderBy] | None) -> list[OrderBy]:
        """Extract expressions that must be selected for a DISTINCT order by."""
        if not orderby:
            return []

        if isinstance(orderby, (list, tuple, set)):
            return [field for item in orderby for field in self._selectable_orderby_fields(item)]

        if isinstance(orderby, str):
            expression = orderby.rstrip()
            for nulls_order in (" NULLS FIRST", " NULLS LAST"):
                if expression.upper().endswith(nulls_order):
                    expression = expression[: -len(nulls_order)].rstrip()
                    break

            expression_without_direction, _, direction = expression.rpartition(" ")
            return [expression_without_direction if direction.upper() in {"ASC", "DESC"} else orderby]

        if isinstance(orderby, Field):
            return t.cast(list[OrderBy], [orderby])

        fields = []
        first = getattr(orderby, "first", None)
        second = getattr(orderby, "second", None)

        if first is not None:  # pragma: no branch - an orderby expression with a second part has a first
            fields.extend(self._selectable_orderby_fields(first))
        if second is not None:
            fields.extend(self._selectable_orderby_fields(second))

        return fields

    def _select_distinct_ids_with_orderby_fields(self, query: Query, select_kwargs: SelectKwargs) -> str:
        db = self._get_db()
        model = self.model
        id_field = t.cast(TypedField[int], model.id)

        # named, because a joined table's id can be among the orderby fields too:
        select_args: list[OrderBy] = [t.cast(OrderBy, id_field.with_alias("typedal_paginate_id"))]
        seen = {str(model.id)}

        for field in self._selectable_orderby_fields(select_kwargs.get("orderby")):
            key = str(field)
            if key not in seen:
                select_args.append(field)
                seen.add(key)

        kwargs = select_kwargs.copy()
        limitby = kwargs.pop("limitby", None)
        orderby = kwargs.pop("orderby")
        ordering = orderby if isinstance(orderby, (list, tuple)) else [orderby]
        sql_orderby = ", ".join(db._adapter.expand(field) for field in ordering)
        # Rank joined rows before reducing them to one entry per root. The first
        # occurrence in the requested ordering determines each root's page.
        select_args.append(f"ROW_NUMBER() OVER (ORDER BY {sql_orderby}) AS typedal_paginate_position")
        kwargs["distinct"] = False
        ids = db(query)._select(*select_args, **kwargs).rstrip(";")
        dialect = db._adapter.dialect
        id_column = dialect.quote("typedal_paginate_id")
        position_column = dialect.quote("typedal_paginate_position")
        limited_ids = dialect.select(
            id_column,
            f"({ids}) AS typedal_paginate_ids",
            groupby=id_column,
            orderby=f"MIN({position_column})",
            limitby=limitby,
        ).rstrip(";")
        # MySQL requires an unlimited outer SELECT for a limited IN subquery.
        return dialect.select(id_column, f"({limited_ids}) AS typedal_paginate_limited_ids")

    def _apply_limitby_optimization(
        self,
        query: Query,
        select_kwargs: SelectKwargs,
        joins: list[t.Any],
        metadata: Metadata,
        left_joins: list[Expression] | None = None,
    ) -> Query:
        """
        Apply limitby optimization when relationships are present.

        `left_joins` are the left joins used by filtering and ordering, see _required_left_joins.
        """
        if not (limitby := select_kwargs.pop("limitby", ())):
            return query

        db = self._get_db()
        model = self.model

        kwargs: SelectKwargs = select_kwargs.copy()
        kwargs["limitby"] = limitby

        if joins:
            kwargs["join"] = joins
        if left_joins:
            kwargs["left"] = left_joins
        if joins or left_joins:
            kwargs["distinct"] = True
        if left_joins and not kwargs.get("orderby") and kwargs.get("orderby_on_limitby", True):
            # pydal's implicit limitby order includes the left-joined ids, which DISTINCT doesn't select:
            kwargs["orderby"] = t.cast(OrderBy, model.id)

        if (joins or left_joins) and kwargs.get("orderby"):
            ids = self._select_distinct_ids_with_orderby_fields(query, kwargs)
        else:
            ids = db(query)._select(model.id, **kwargs)

        id_field = t.cast(TypedField[int], model.id)

        query &= id_field.belongs(ids)
        metadata["ids"] = ids

        return query

    def _build_left_joins_and_fields(self, select_args: list[t.Any], left_joins: list[Expression]) -> list[t.Any]:
        """
        Build left joins and ensure required fields are selected.
        """
        for key, relation in self.relationships.items():
            select_args = self._process_relationship_for_left_join(relation, key, select_args, left_joins, self.model)

        return select_args

    def _process_relationship_for_left_join(
        self,
        relation: Relationship[t.Any],
        key: str,
        select_args: list[t.Any],
        left_joins: list[Expression],
        parent_table: t.Type[_TypedTable],
        parent_key: str = "",
    ) -> list[t.Any]:
        """Process a single relationship for left join and field selection."""
        db = self._get_db()
        other = relation.get_table(db)
        method: JOIN_OPTIONS = relation.join or DEFAULT_JOIN_OPTION

        select_fields = ", ".join([str(_) for _ in select_args])
        pre_alias = str(other)
        # Self-referencing relationship: 'other' is the same table as 'parent_table' (or, for a nested relationship
        # like 'writer.articles', as the root table), so the name-based helpers below can't tell their fields apart.
        # Alias upfront and skip them.
        is_self_reference = other is parent_table or other is self.model

        if is_self_reference:
            other = other.with_alias(f"{key}_{hash(relation)}")
            select_args = [*select_args, other.ALL]
        else:
            # Ensure required fields are selected
            select_args = self._ensure_relationship_fields(select_args, other, select_fields)

        # Build join condition
        if relation.on:
            # Custom .on condition - always left join
            # (for is_self_reference, 'other' was already aliased above so the on() callback
            # receives distinguishable tables for both sides of the join)
            on = relation.on(parent_table, other)  # ty: ignore[invalid-argument-type]
            if not isinstance(on, list):
                on = [on]

            on = [_ for _ in on if isinstance(_, Expression)]
            left_joins.extend(on)
        elif method == "left":
            # Generate left join condition
            if not is_self_reference:
                other = other.with_alias(f"{key}_{hash(relation)}")
            condition = t.cast(Query, relation.condition(parent_table, other))  # ty: ignore[call-non-callable, invalid-argument-type]

            if callable(relation.condition_and):
                condition &= relation.condition_and(parent_table, other)  # ty: ignore[invalid-argument-type]

            left_joins.append(other.on(condition))
        else:
            # Inner join (handled in _build_inner_joins)
            if not is_self_reference:  # pragma: no branch - self references were aliased above
                other = other.with_alias(f"{key}_{hash(relation)}")

        # Handle aliasing in select_args
        if not is_self_reference:
            select_args = self._update_select_args_with_alias(select_args, pre_alias, other)

        # Process nested relationships
        for nested_name, nested in relation.nested.items():
            # todo: add additional test, deduplicate
            nested_key = f"{parent_key}.{nested_name}" if parent_key else f"{key}.{nested_name}"
            select_args = self._process_relationship_for_left_join(
                nested,
                nested_name,
                select_args,
                left_joins,
                other,
                nested_key,
            )

        return select_args

    def _ensure_relationship_fields(
        self,
        select_args: list[t.Any],
        other: t.Type[TypedTable],
        select_fields: str,
    ) -> list[t.Any]:
        """Ensure required fields from relationship table are selected."""
        if f"{other}." not in select_fields:
            # No fields of other selected, add .ALL
            select_args.append(other.ALL)
        elif f"{other}.id" not in select_fields:
            # Fields of other selected, but required ID is missing
            select_args.append(other.id)

        return select_args

    def _update_select_args_with_alias(
        self,
        select_args: list[t.Any],
        pre_alias: str,
        other: t.Type[TypedTable],
    ) -> list[t.Any]:
        """Update select_args to use aliased table names."""
        post_alias = str(other).split(" AS ")[-1]

        if pre_alias != post_alias:
            updated_args = []
            for arg in select_args:
                if isinstance(arg, SQLALL):
                    updated_args.extend(str(field).replace(f"{pre_alias}.", f"{post_alias}.") for field in arg._table)
                else:
                    updated_args.append(str(arg).replace(f"{pre_alias}.", f"{post_alias}."))
            select_args = updated_args

        return select_args

    def _collect_with_relationships(
        self,
        rows: Rows,
        metadata: Metadata,
        _to: t.Type["TypedRows[T_MetaInstance]"],
        _into: t.Type[_TypedTable] | None = None,
        _init: t.Callable[[_TypedTable, Row], None] | None = None,
    ) -> "TypedRows[T_MetaInstance]":
        """
        Transform the raw rows into Typed Table model instances with nested relationships.
        """
        db = self._get_db()
        main_table = self._ensure_table_defined()
        into = _into or self.model

        # id: Model
        records: dict[t.Any, T_MetaInstance] = {}

        # id: [Row]
        raw_per_id: dict[t.Any, list[t.Any]] = defaultdict(list)

        # Track what we've seen: (parent record, column, relation id) -> instance
        seen_relations: SeenRelations = {}

        for row in rows:
            main = row[main_table]
            main_id = main.id

            raw_per_id[main_id].append(normalize_table_keys(row))

            if main_id not in records:
                records[main_id] = t.cast(T_MetaInstance, into(main))
                if _init:
                    _init(t.cast(_TypedTable, records[main_id]), row)
                records[main_id]._with = list(self.relationships.keys())

                # Setup all relationship defaults (once)
                for col, relationship in self.relationships.items():
                    records[main_id][col] = [] if relationship.multiple else None

            # Process each top-level relationship
            for column, relation in self.relationships.items():
                self._process_relationship_data(
                    row=row,
                    column=column,
                    relation=relation,
                    parent_record=records[main_id],
                    seen_relations=seen_relations,
                    db=db,
                )

        return _to(rows, self.model, records, metadata=metadata, raw=raw_per_id)

    def _process_relationship_data(
        self,
        row: t.Any,
        column: str,
        relation: Relationship[t.Any],
        parent_record: t.Any,
        seen_relations: SeenRelations,
        db: t.Any,
        path: str = "",
    ) -> t.Any | None:
        """
        Process relationship data from a row and attach it to the parent record.

        Returns the created instance (for nested processing).

        Args:
            row: The database row containing relationship data
            column: The relationship column name
            relation: The Relationship object
            parent_record: The parent model instance to attach data to
            seen_relations: Dict tracking which relationships we've already processed
            db: Database instance
            path: Current relationship path (e.g., "users.bestie")

        Returns:
            The created relationship instance, or None if skipped (no data, or already attached)
        """
        # Build the full path for tracking (e.g., "users", "users.bestie", "users.bestie.articles")
        current_path = f"{path}.{column}" if path else column

        # Get the relationship column name (with hash for alias)
        relationship_column = f"{column}_{hash(relation)}"

        # Get relation data from row
        relation_data = row[relationship_column] if relationship_column in row else row.get(relation.get_table_name())

        # Skip if no data or NULL id
        if not relation_data or relation_data.id is None:
            return None

        # Check if we've already attached this relationship instance to this parent. Keyed by the parent object:
        # the same row (e.g. an author) reached through different parents is a separate instance for each of them.
        seen_key = (id(parent_record), current_path, relation_data.id)
        if (seen := seen_relations.get(seen_key)) is not None:
            # a later row can still hold new data for its nested relationships (e.g. the author's second article)
            if relation.nested:
                self._process_nested_relationships(row, relation, seen, seen_relations, db, current_path)
            return None

        # Create the relationship instance
        relation_table = relation.get_table(db)
        instance = relation_table(relation_data) if looks_like(relation_table, TypedTable) else relation_data
        seen_relations[seen_key] = instance

        # Process nested relationships on this instance
        if relation.nested:
            self._process_nested_relationships(
                row=row,
                relation=relation,
                instance=instance,
                seen_relations=seen_relations,
                db=db,
                path=current_path,
            )

        # Attach to parent
        if relation.multiple:
            # current_value = parent_record.get(column)
            # if not isinstance(current_value, list):
            #     setattr(parent_record, column, [])
            parent_record[column].append(instance)
        else:
            parent_record[column] = instance

        return instance

    def _process_nested_relationships(
        self,
        row: t.Any,
        relation: Relationship[t.Any],
        instance: t.Any,
        seen_relations: SeenRelations,
        db: t.Any,
        path: str,
    ) -> None:
        """
        Process all nested relationships for a given instance.

        Args:
            row: The database row containing relationship data
            relation: The parent Relationship object containing nested relationships
            instance: The instance to attach nested data to
            seen_relations: Dict tracking which relationships we've already processed
            db: Database instance
            path: Current relationship path
        """
        # Initialize nested relationship defaults on the instance
        # Use __dict__ to avoid triggering __get__ descriptors
        for nested_col, nested_relation in relation.nested.items():
            if nested_col not in instance.__dict__:
                instance.__dict__[nested_col] = [] if nested_relation.multiple else None

        # Process each nested relationship
        for nested_col, nested_relation in relation.nested.items():
            self._process_relationship_data(
                row=row,
                column=nested_col,
                relation=nested_relation,
                parent_record=instance,
                seen_relations=seen_relations,
                db=db,
                path=path,
            )

    def collect_or_fail(self, exception: t.Optional[Exception] = None) -> TypedRows[T_MetaInstance]:
        """
        Call .collect() and raise an error if nothing found.

        Basically unwraps t.Optional type.
        """
        return self.collect() or throw(exception or ValueError("Nothing found!"))

    def __iter__(self) -> t.Generator[T_MetaInstance, None, None]:  # ty: ignore[invalid-method-override]
        """
        You can start iterating a Query Builder object before calling collect, for ease of use.
        """
        builder = self._extend(metadata={"iterating": True})
        window_size = self.metadata.get("window_size", None)

        if not window_size:
            yield from builder.collect()
            return

        for chunk in builder.chunk(window_size):
            yield from chunk

    def __await__(self):
        """`await builder` collects the rows asynchronously."""
        return self.collect_async().__await__()

    async def __aiter__(self):
        """`async for row in builder` yields rows, in windows when `.window()` was used."""
        builder = self._extend(metadata={"iterating": True})

        window_size = self.metadata.get("window_size", None)

        if window_size:
            async for chunk in builder.chunk_async(window_size):
                for row in chunk:
                    yield row
        else:
            rows = await builder.collect_async()
            for row in rows:
                yield row

    def window(self, window_size: int) -> QueryBuilder[T_MetaInstance]:
        """Iterate in chunks of `window_size` rows instead of loading everything at once."""
        return self._extend(metadata={"window_size": window_size})

    def __count(
        self,
        db: TypeDAL,
        distinct: bool | Field | TypedField[t.Any] = False,
        *,
        include_left_for_distinct: bool = True,
    ) -> tuple[Query | str, bool | Field | TypedField[t.Any]]:
        """
        Internal, shared logic between .count and ._count.

        Returns the query to count, or the full SQL when the query filters on a left-joined table
        (pydal's count can't LEFT JOIN), plus the `distinct` to count with.
        """
        model = self.model
        joined = self._joined_tables()
        rewrites = self._alias_rewrites(joined)
        query = self._resolve_query(joined, rewrites)
        if rewrites and isinstance(distinct, (Field, Expression)):
            distinct = _rewrite_tables(distinct, rewrites)

        for table in self.cross_joins:
            query &= table.id > 0

        left_joins: list[Expression] = []
        if names := self._required_join_names(joined, query, distinct):
            self._build_left_joins_and_fields([], left_joins)
        if left_joins := self._required_left_joins(names, left_joins):
            inner_joins = self._build_inner_joins()
            self._validate_joins(query, [], [model.id], {"join": inner_joins, "left": left_joins}, joined)
            options: SelectKwargs = {"left": left_joins, "distinct": bool(distinct)}
            if inner_joins:
                options["join"] = inner_joins
            counted = t.cast(Field, distinct if isinstance(distinct, (Field, Expression)) else model.id)
            # COUNT(column) skips the NULL a left join yields, like COUNT(DISTINCT column) does:
            subquery = db(query)._select(counted.with_alias("typedal_counted"), **options).rstrip().rstrip(";")
            column = db._adapter.dialect.quote("typedal_counted")
            return f"SELECT COUNT({column}) FROM ({subquery}) AS typedal_count;", distinct  # noqa: S608
            # subquery is generated by pydal

        conditions: list[QueryLike] = [join.second for join in self._build_inner_joins()]
        for key, relation in self.relationships.items():
            if not relation.condition:
                continue

            include_left_join = distinct and include_left_for_distinct
            if relation.join == "inner" or not include_left_join:
                continue

            # same alias as the select uses, so a filter on this relationship's table applies to this join:
            other = relation.get_table(db).with_alias(f"{key}_{hash(relation)}")

            conditions.append(relation.condition(model, other))  # ty: ignore[invalid-argument-type]
            if callable(relation.condition_and):
                conditions.append(relation.condition_and(model, other))  # ty: ignore[invalid-argument-type]

        self._validate_joins(query, conditions, [model.id], {}, joined)
        for condition in conditions:
            query &= condition
        return query, distinct

    def __execute_count(self, db: TypeDAL, query: Query | str, distinct: bool | Field | TypedField[t.Any]) -> int:
        if isinstance(query, str):
            return int(db.executesql(query)[0][0])
        return db(query).count(distinct)

    def count(self, distinct: bool | Field | TypedField[t.Any] = False) -> int:
        """
        Return the amount of rows matching the current query.

        Passing a field counts its distinct values.
        """
        require_permission(self._permissions, "read")
        db = self._get_db()
        query, distinct = self.__count(db, distinct=distinct)

        return self.__execute_count(db, query, distinct)

    def _count(self, distinct: bool | Field | TypedField[t.Any] = False) -> str:
        """
        Return the SQL for .count().
        """
        db = self._get_db()
        query, distinct = self.__count(db, distinct=distinct)

        return query if isinstance(query, str) else db(query)._count(distinct)

    def exists(self) -> bool:
        """
        Determines if t.Any records exist matching the current query.

        Returns True if one or more records exist; otherwise, False.

        Returns:
            bool: A boolean indicating whether t.Any records exist.
        """
        require_permission(self._permissions, "read")
        return bool(self.count())

    def __pagination_count(self) -> int:
        if not self.relationships:
            return self.count()

        db = self._get_db()
        distinct = t.cast(TypedField[int], self.model.id)
        query, distinct = self.__count(db, distinct=distinct, include_left_for_distinct=False)
        return self.__execute_count(db, query, distinct)

    def __paginate(
        self,
        limit: int,
        page: int = 1,
    ) -> "QueryBuilder[T_MetaInstance]":
        available = self.__pagination_count()

        _from = limit * (page - 1)
        _to = (limit * page) if limit else available

        metadata: Metadata = {}

        metadata["pagination"] = {
            "limit": limit,
            "current_page": page,
            "max_page": math.ceil(available / limit) if limit else 1,
            "rows": available,
            "min_max": (_from, _to),
        }

        return self._extend(select_kwargs={"limitby": (_from, _to)}, metadata=metadata)

    def paginate(self, limit: int, page: int = 1, verbose: bool = False) -> "PaginatedRows[T_MetaInstance]":
        """
        Paginate transforms the more readable `page` and `limit` to pydals internal limit and offset.

        Note: when using relationships, this limit is only applied to the 'main' table and t.Any number of extra rows \
            can be loaded with relationship data!
        """
        require_permission(self._permissions, "read")
        builder = self.__paginate(limit, page)

        rows = t.cast(PaginatedRows[T_MetaInstance], builder.collect(verbose=verbose, _to=PaginatedRows))

        rows._query_builder = builder
        return rows

    def _paginate(
        self,
        limit: int,
        page: int = 1,
    ) -> str:
        builder = self.__paginate(limit, page)
        return builder._collect()

    def chunk(self, chunk_size: int) -> t.Generator[TypedRows[T_MetaInstance], t.Any, None]:
        """
        Generator that yields rows from a paginated source in chunks.

        This function retrieves rows from a paginated data source in chunks of the
        specified `chunk_size` and yields them as TypedRows.

        Example:
            ```
            for chunk_of_rows in Table.where(SomeTable.id > 5).chunk(100):
                for row in chunk_of_rows:
                    # Process each row within the chunk.
                    pass
            ```
        """
        # require_permission checked in .collect()
        if "limitby" in self.select_kwargs:
            raise ValueError("chunk() cannot be combined with an existing limitby")

        page = 1

        while rows := self.__paginate(chunk_size, page).collect():
            yield rows
            page += 1

    def first(self, verbose: bool = False) -> T_MetaInstance | None:
        """
        Get the first row matching the currently built query.

        Also adds paginate, since it would be a waste to select more rows than needed.
        """
        require_permission(self._permissions, "read")
        row = self.paginate(page=1, limit=1, verbose=verbose).first()
        if not row:
            return None

        if not isinstance(self.model, TableMeta):
            # old-style pydal table: keep pydal semantics and return raw Row
            return row

        return self.model.from_row(row)  # ty: ignore[invalid-argument-type]

    def _first(self) -> str:
        return self._paginate(page=1, limit=1)

    def first_or_fail(self, exception: t.Optional[BaseException] = None, verbose: bool = False) -> T_MetaInstance:
        """
        Call .first() and raise an error if nothing found.

        Basically unwraps t.Optional type.
        """
        require_permission(self._permissions, "read")
        return self.first(verbose=verbose) or throw(exception or ValueError("Nothing found!"))

    ###############
    # async twins #
    ###############
    # Every method below offloads its *whole* sync counterpart to a worker thread
    # (see `typedal.asynchronous`), so relationships, caching, hooks and permissions behave
    # identically - there is no second implementation of any of it. Outside an
    # `async with db.session()` block each call commits on its own; inside one they all share
    # the session's connection and transaction.

    async def delete_async(self) -> list[int]:
        """
        Async twin of `delete()`.
        """
        return await run_async(self._get_db(), self.delete)

    async def update_async(self, **fields: t.Any) -> list[int]:
        """
        Async twin of `update()`.
        """
        return await run_async(self._get_db(), self.update, **fields)

    async def execute_async(self, add_id: bool = False) -> Rows:
        """
        Async twin of `execute()`.
        """
        return await run_async(self._get_db(), self.execute, add_id=add_id)

    async def collect_async(
        self,
        verbose: bool = False,
        _to: t.Type["TypedRows[t.Any]"] | None = None,
        add_id: bool = True,
        _into: t.Type[_TypedTable] | None = None,
        _init: t.Callable[[_TypedTable, Row], None] | None = None,
    ) -> TypedRows[T_MetaInstance]:
        """
        Async twin of `collect()`.
        """
        return await run_async(
            self._get_db(),
            self.collect,
            verbose=verbose,
            _to=_to,
            add_id=add_id,
            _into=_into,
            _init=_init,
        )

    async def collect_into_async[T_Into: _TypedTable](
        self,
        into: t.Type[T_Into],
        verbose: bool = False,
        add_id: bool = True,
        init: t.Callable[[T_Into, Row], None] | None = None,
    ) -> TypedRows[T_Into]:
        """
        Async twin of `collect_into()`.
        """
        return await run_async(  # ty: ignore[invalid-return-type]
            self._get_db(),
            self.collect_into,
            into,
            verbose=verbose,
            add_id=add_id,
            init=init,
        )

    async def collect_or_fail_async(self, exception: t.Optional[Exception] = None) -> TypedRows[T_MetaInstance]:
        """
        Async twin of `collect_or_fail()`.
        """
        return await self.collect_async() or throw(exception or ValueError("Nothing found!"))

    @t.overload
    async def column_async[T: t.Any](self, field: TypedField[T], **options: t.Unpack[SelectKwargs]) -> list[T]:
        """
        Get all values in a typed field asynchronously.
        """

    @t.overload
    async def column_async[T: t.Any](self, field: T, **options: t.Unpack[SelectKwargs]) -> list[T]:
        """
        Get all values in an untyped field asynchronously.
        """

    async def column_async[T: t.Any](self, field: TypedField[T] | T, **options: t.Unpack[SelectKwargs]) -> list[T]:
        """
        Async twin of `column()`.
        """
        # `column` is overloaded, which a ParamSpec cannot bind to:
        column = t.cast(t.Callable[..., list[T]], self.column)
        return await run_async(self._get_db(), column, field, **options)

    async def count_async(self, distinct: bool | Field | TypedField[t.Any] = False) -> int:
        """
        Async twin of `count()`.
        """
        return await run_async(self._get_db(), self.count, distinct)

    async def exists_async(self) -> bool:
        """
        Async twin of `exists()`.
        """
        return await run_async(self._get_db(), self.exists)

    async def paginate_async(self, limit: int, page: int = 1, verbose: bool = False) -> "PaginatedRows[T_MetaInstance]":
        """
        Async twin of `paginate()`.
        """
        return await run_async(self._get_db(), self.paginate, limit=limit, page=page, verbose=verbose)

    async def chunk_async(self, chunk_size: int) -> t.AsyncGenerator[TypedRows[T_MetaInstance], None]:
        """
        Async twin of `chunk()`.

        One page per await. Outside a session each page is its own transaction, so a concurrent
        writer can be visible halfway through the iteration; wrap it in `db.session()` if you
        need every page to come from the same snapshot.
        """
        # require_permission checked in .collect()
        if "limitby" in self.select_kwargs:
            raise ValueError("chunk_async() cannot be combined with an existing limitby")

        db = self._get_db()
        page = 1

        def fetch_page(number: int) -> TypedRows[T_MetaInstance]:
            # on the worker thread: `__paginate` runs a count query of its own
            return self.__paginate(chunk_size, number).collect()

        while rows := await run_async(db, fetch_page, page):
            yield rows
            page += 1

    async def first_async(self, verbose: bool = False) -> T_MetaInstance | None:
        """
        Async twin of `first()`.
        """
        return await run_async(self._get_db(), self.first, verbose=verbose)

    async def first_or_fail_async(
        self,
        exception: t.Optional[BaseException] = None,
        verbose: bool = False,
    ) -> T_MetaInstance:
        """
        Async twin of `first_or_fail()`.
        """
        return await self.first_async(verbose=verbose) or throw(exception or ValueError("Nothing found!"))


# note: these imports exist at the bottom of this file to prevent circular import issues:

from .caching import (  # noqa: E402
    create_and_hash_cache_key,
    get_expire,
    load_from_cache,
    save_to_cache,
)
from .relationships import Relationship  # noqa: E402
from .rows import PaginatedRows, TypedRows  # noqa: E402
