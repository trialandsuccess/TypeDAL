"""
Contains base functionality related to the Query Builder.
"""

from __future__ import annotations

import datetime as dt
import math
import typing as t
from collections import defaultdict

import pydal.objects

from .constants import DEFAULT_JOIN_OPTION, JOIN_OPTIONS
from .core import TypeDAL
from .fields import TypedField, is_typed_field
from .helpers import DummyQuery, as_lambda, looks_like, normalize_table_keys, throw
from .tables import TypedTable
from .types import (
    CacheMetadata,
    Condition,
    Expression,
    Field,
    Metadata,
    OnQuery,
    OrderBy,
    Query,
    Rows,
    SelectKwargs,
    T,
    T_MetaInstance,
)


class QueryBuilder(t.Generic[T_MetaInstance]):
    """
    Abstration on top of pydal's query system.
    """

    model: t.Type[T_MetaInstance]
    query: Query
    select_args: list[t.Any]
    select_kwargs: SelectKwargs
    relationships: dict[str, Relationship[t.Any]]
    metadata: Metadata

    def __init__(
        self,
        model: t.Type[T_MetaInstance],
        add_query: t.Optional[Query] = None,
        select_args: t.Optional[list[t.Any]] = None,
        select_kwargs: t.Optional[SelectKwargs] = None,
        relationships: dict[str, Relationship[t.Any]] = None,
        metadata: Metadata = None,
    ):
        """
        Normally, you wouldn't manually initialize a QueryBuilder but start using a method on a TypedTable.

        Example:
            MyTable.where(...) -> QueryBuilder[MyTable]
        """
        self.model = model
        table = model._ensure_table_defined()
        default_query = t.cast(Query, table.id > 0)
        self.query = add_query or default_query
        self.select_args = select_args or []
        self.select_kwargs = select_kwargs or {}
        self.relationships = relationships or {}
        self.metadata = metadata or {}

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
        table = self.model._ensure_table_defined()
        default_query = t.cast(Query, table.id > 0)
        return any(
            [
                self.query != default_query,
                self.select_args,
                self.select_kwargs,
                self.relationships,
                self.metadata,
            ],
        )

    def _extend(
        self,
        add_query: t.Optional[Query] = None,
        overwrite_query: t.Optional[Query] = None,
        select_args: t.Optional[list[t.Any]] = None,
        select_kwargs: t.Optional[SelectKwargs] = None,
        relationships: dict[str, Relationship[t.Any]] = None,
        metadata: Metadata = None,
    ) -> "QueryBuilder[T_MetaInstance]":
        return QueryBuilder(
            self.model,
            (add_query & self.query) if add_query else overwrite_query or self.query,
            (self.select_args + select_args) if select_args else self.select_args,
            (self.select_kwargs | select_kwargs) if select_kwargs else self.select_kwargs,
            (self.relationships | relationships) if relationships else self.relationships,
            (self.metadata | (metadata or {})) if metadata else self.metadata,
        )

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
        """
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

    def where(
        self,
        *queries_or_lambdas: Query | t.Callable[[t.Type[T_MetaInstance]], Query] | dict[str, t.Any],
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
        """
        new_query = self.query
        table = self.model._ensure_table_defined()

        queries_or_lambdas = (
            *queries_or_lambdas,
            filters,
        )

        subquery = t.cast(Query, DummyQuery())
        for query_part in queries_or_lambdas:
            if isinstance(query_part, (Field, pydal.objects.Field)) or is_typed_field(query_part):
                subquery |= t.cast(Query, query_part != None)
            elif isinstance(query_part, (pydal.objects.Query, Expression, pydal.objects.Expression)):
                subquery |= t.cast(Query, query_part)
            elif callable(query_part):
                if result := query_part(self.model):
                    subquery |= result
            elif isinstance(query_part, dict):
                subsubquery = DummyQuery()
                for field, value in query_part.items():
                    subsubquery &= table[field] == value
                if subsubquery:
                    subquery |= subsubquery
            else:
                raise ValueError(f"Unexpected query type ({type(query_part)}).")

        if subquery:
            new_query &= subquery

        return self._extend(overwrite_query=new_query)

    def _parse_relationships(
        self, fields: t.Iterable[str | t.Type[TypedTable]], method: JOIN_OPTIONS = None, **update: t.Any
    ) -> dict[str, Relationship[t.Any]]:
        """
        Parse relationship fields into a dict of base relationships with nested relationships.

        Args:
            fields: Iterable of relationship field names
                (e.g., ['relationship', 'relationship.with_nested', 'relationship.no2'])
            condition_and: Optional condition to pass to relationship clones

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
        *fields: str | t.Type[TypedTable],
        method: JOIN_OPTIONS = None,
        on: OnQuery | list[Expression] | Expression = None,
        condition: Condition = None,
        condition_and: Condition = None,
    ) -> "QueryBuilder[T_MetaInstance]":
        """
        Include relationship fields in the result.

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
        relationships = self.model.get_relationships()

        if condition and on:
            raise ValueError("condition and on can not be used together!")
        elif condition:
            if len(fields) != 1:
                raise ValueError("join(field, condition=...) can only be used with exactly one field!")

            if isinstance(condition, pydal.objects.Query):
                condition = as_lambda(condition)

            to_field = t.cast(t.Type[TypedTable], fields[0])
            relationships = {
                str(to_field): Relationship(to_field, condition=condition, join=method, condition_and=condition_and)
            }
        elif on:
            if len(fields) != 1:
                raise ValueError("join(field, on=...) can only be used with exactly one field!")

            if isinstance(on, pydal.objects.Expression):
                on = [on]

            if isinstance(on, list):
                on = as_lambda(on)

            to_field = t.cast(t.Type[TypedTable], fields[0])
            relationships = {str(to_field): Relationship(to_field, on=on, join=method, condition_and=condition_and)}

        else:
            if fields:
                # join on every relationship
                # simple: 'relationship'
                #   -> {'relationship': Relationship('relationship')}
                # complex with one: relationship.with_nested
                #   -> {'relationship': Relationship('relationship', nested=[Relationship('with_nested')])
                # complex with two:  relationship.with_nested,  relationship.no2
                #   -> {'relationship': Relationship('relationship',
                #                           nested=[Relationship('with_nested'), Relationship('no2')])

                relationships = self._parse_relationships(fields, method=method, condition_and=condition_and)

            if method:
                relationships = {
                    str(k): r.clone(join=method, condition_and=condition_and) for k, r in relationships.items()
                }

        return self._extend(relationships=relationships)

    def cache(
        self,
        *deps: t.Any,
        expires_at: t.Optional[dt.datetime] = None,
        ttl: t.Optional[int | dt.timedelta] = None,
    ) -> "QueryBuilder[T_MetaInstance]":
        """
        Enable caching for this query to load repeated calls from a dill row \
            instead of executing the sql and collecing matching rows again.
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

    def delete(self) -> list[int]:
        """
        Based on the current query, delete rows and return a list of deleted IDs.
        """
        db = self._get_db()
        removed_ids = [_.id for _ in db(self.query).select("id")]
        if db(self.query).delete():
            # success!
            return removed_ids

        return []

    def _delete(self) -> str:
        db = self._get_db()
        return str(db(self.query)._delete())

    def update(self, **fields: t.Any) -> list[int]:
        """
        Based on the current query, update `fields` and return a list of updated IDs.
        """
        # todo: limit?
        db = self._get_db()
        updated_ids = db(self.query).select("id").column("id")
        if db(self.query).update(**fields):
            # success!
            return updated_ids

        return []

    def _update(self, **fields: t.Any) -> str:
        db = self._get_db()
        return str(db(self.query)._update(**fields))

    def _before_query(self, mut_metadata: Metadata, add_id: bool = True) -> tuple[Query, list[t.Any], SelectKwargs]:
        select_args = [self._select_arg_convert(_) for _ in self.select_args] or [self.model.ALL]
        select_kwargs = self.select_kwargs.copy()
        query = self.query
        model = self.model
        mut_metadata["query"] = query
        # require at least id of main table:
        select_fields = ", ".join([str(_) for _ in select_args])
        tablename = str(model)

        if add_id and f"{tablename}.id" not in select_fields:
            # fields of other selected, but required ID is missing.
            select_args.append(model.id)

        if self.relationships:
            query, select_args = self._handle_relationships_pre_select(query, select_args, select_kwargs, mut_metadata)

        return query, select_args, select_kwargs

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

    def _collect_cached(self, metadata: Metadata) -> "TypedRows[T_MetaInstance] | None":
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
            metadata,
            self.query,
            self.select_args,
            self.select_kwargs,
            self.relationships.keys(),
        )

        # re-set after creating key:
        metadata["cache"]["expires_at"] = expires_at
        metadata["cache"]["key"] = key

        return load_from_cache(key, self._get_db())

    def execute(self, add_id: bool = False) -> Rows:
        """
        Raw version of .collect which only executes the SQL, without performing t.Any magic afterwards.
        """
        db = self._get_db()
        metadata = t.cast(Metadata, self.metadata.copy())

        query, select_args, select_kwargs = self._before_query(metadata, add_id=add_id)

        return db(query).select(*select_args, **select_kwargs)

    def collect(
        self,
        verbose: bool = False,
        _to: t.Type["TypedRows[t.Any]"] = None,
        add_id: bool = True,
    ) -> "TypedRows[T_MetaInstance]":
        """
        Execute the built query and turn it into model instances, while handling relationships.
        """
        if _to is None:
            _to = TypedRows

        db = self._get_db()
        metadata = t.cast(Metadata, self.metadata.copy())

        if metadata.get("cache", {}).get("enabled") and (result := self._collect_cached(metadata)):
            return result

        query, select_args, select_kwargs = self._before_query(metadata, add_id=add_id)

        metadata["sql"] = db(query)._select(*select_args, **select_kwargs)

        if verbose:  # pragma: no cover
            print(metadata["sql"])

        rows: Rows = db(query).select(*select_args, **select_kwargs)

        metadata["final_query"] = str(query)
        metadata["final_args"] = [str(_) for _ in select_args]
        metadata["final_kwargs"] = select_kwargs

        if verbose:  # pragma: no cover
            print(rows)

        if not self.relationships:
            # easy
            typed_rows = _to.from_rows(rows, self.model, metadata=metadata)

        else:
            # harder: try to match rows to the belonging objects
            # assume structure of {'table': <data>} per row.
            # if that's not the case, return default behavior again
            typed_rows = self._collect_with_relationships(rows, metadata=metadata, _to=_to)

        # only saves if requested in metadata:
        return save_to_cache(typed_rows, rows)

    @t.overload
    def column(self, field: TypedField[T], **options: t.Unpack[SelectKwargs]) -> list[T]:
        """
        If a typedfield is passed, the output type can be safely determined.
        """

    @t.overload
    def column(self, field: T, **options: t.Unpack[SelectKwargs]) -> list[T]:
        """
        Otherwise, the output type is loosely determined (assumes `field: type` or t.Any).
        """

    def column(self, field: TypedField[T] | T, **options: t.Unpack[SelectKwargs]) -> list[T]:
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
    ) -> tuple[Query, list[t.Any]]:
        """Handle relationship joins and field selection for database query."""
        # Collect all relationship keys including nested ones
        metadata["relationships"] = self._collect_all_relationship_keys()

        # Build joins and apply limitby optimization if needed
        inner_joins = self._build_inner_joins()
        query = self._apply_limitby_optimization(query, select_kwargs, inner_joins, metadata)

        if inner_joins:
            select_kwargs["join"] = inner_joins

        # Build left joins and handle field selection
        left_joins: list[Expression] = []
        select_args = self._build_left_joins_and_fields(select_args, left_joins)

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
        self, relation: Relationship[t.Any], parent_table: t.Type[TypedTable], key: str, parent_key: str = ""
    ) -> list[t.Any]:
        """Recursively build inner joins for a relationship and its nested relationships."""
        db = self._get_db()
        joins = []

        # Handle current level
        if relation.condition and relation.join == "inner":
            other = relation.get_table(db)
            other = other.with_alias(f"{key}_{hash(relation)}")
            condition = relation.condition(parent_table, other)

            if callable(relation.condition_and):
                condition &= relation.condition_and(parent_table, other)

            joins.append(other.on(condition))

            # Process nested relationships
            for nested_name, nested in relation.nested.items():
                # todo: add additional test, deduplicate
                nested_key = f"{parent_key}.{nested_name}" if parent_key else f"{key}.{nested_name}"
                joins.extend(self._build_inner_joins_recursive(nested, other, nested_name, nested_key))

        return joins

    def _apply_limitby_optimization(
        self,
        query: Query,
        select_kwargs: SelectKwargs,
        joins: list[t.Any],
        metadata: Metadata,
    ) -> Query:
        """Apply limitby optimization when relationships are present."""
        if not (limitby := select_kwargs.pop("limitby", ())):
            return query

        db = self._get_db()
        model = self.model

        kwargs: SelectKwargs = select_kwargs.copy()
        kwargs["limitby"] = limitby

        if joins:
            kwargs["join"] = joins

        ids = db(query)._select(model.id, **kwargs)
        query = model.id.belongs(ids)
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
        parent_table: t.Type[TypedTable],
        parent_key: str = "",
    ) -> list[t.Any]:
        """Process a single relationship for left join and field selection."""
        db = self._get_db()
        other = relation.get_table(db)
        method: JOIN_OPTIONS = relation.join or DEFAULT_JOIN_OPTION

        select_fields = ", ".join([str(_) for _ in select_args])
        pre_alias = str(other)

        # Ensure required fields are selected
        select_args = self._ensure_relationship_fields(select_args, other, select_fields)

        # Build join condition
        if relation.on:
            # Custom .on condition - always left join
            on = relation.on(parent_table, other)
            if not isinstance(on, list):
                on = [on]

            on = [_ for _ in on if isinstance(_, pydal.objects.Expression)]
            left_joins.extend(on)
        elif method == "left":
            # Generate left join condition
            other = other.with_alias(f"{key}_{hash(relation)}")
            condition = t.cast(Query, relation.condition(parent_table, other))

            if callable(relation.condition_and):
                condition &= relation.condition_and(parent_table, other)

            left_joins.append(other.on(condition))
        else:
            # Inner join (handled in _build_inner_joins)
            other = other.with_alias(f"{key}_{hash(relation)}")

        # Handle aliasing in select_args
        select_args = self._update_select_args_with_alias(select_args, pre_alias, other)

        # Process nested relationships
        for nested_name, nested in relation.nested.items():
            # todo: add additional test, deduplicate
            nested_key = f"{parent_key}.{nested_name}" if parent_key else f"{key}.{nested_name}"
            select_args = self._process_relationship_for_left_join(
                nested, nested_name, select_args, left_joins, other, nested_key
            )

        return select_args

    def _ensure_relationship_fields(
        self, select_args: list[t.Any], other: t.Type[TypedTable], select_fields: str
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
        self, select_args: list[t.Any], pre_alias: str, other: t.Type[TypedTable]
    ) -> list[t.Any]:
        """Update select_args to use aliased table names."""
        post_alias = str(other).split(" AS ")[-1]

        if pre_alias != post_alias:
            select_fields = ", ".join([str(_) for _ in select_args])
            select_fields = select_fields.replace(f"{pre_alias}.", f"{post_alias}.")
            select_args = select_fields.split(", ")

        return select_args

    def _collect_with_relationships(
        self,
        rows: Rows,
        metadata: Metadata,
        _to: t.Type["TypedRows[T_MetaInstance]"],
    ) -> "TypedRows[T_MetaInstance]":
        """
        Transform the raw rows into Typed Table model instances with nested relationships.
        """
        db = self._get_db()
        main_table = self.model._ensure_table_defined()

        # id: Model
        records: dict[t.Any, T_MetaInstance] = {}

        # id: [Row]
        raw_per_id: dict[t.Any, list[t.Any]] = defaultdict(list)

        # Track what we've seen: main_id -> "column-relation_id"
        seen_relations: dict[str, set[str]] = defaultdict(set)

        for row in rows:
            main = row[main_table]
            main_id = main.id

            raw_per_id[main_id].append(normalize_table_keys(row))

            if main_id not in records:
                records[main_id] = self.model(main)
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
                    parent_id=main_id,
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
        parent_id: t.Any,
        seen_relations: dict[str, set[str]],
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
            parent_id: ID of the parent for tracking
            seen_relations: Dict tracking which relationships we've already processed
            db: Database instance
            path: Current relationship path (e.g., "users.bestie")

        Returns:
            The created relationship instance, or None if skipped
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

        # Check if we've already seen this relationship instance
        seen_key = f"{current_path}-{relation_data.id}"
        if seen_key in seen_relations[parent_id]:
            return None  # Already processed

        seen_relations[parent_id].add(seen_key)

        # Create the relationship instance
        relation_table = relation.get_table(db)
        instance = relation_table(relation_data) if looks_like(relation_table, TypedTable) else relation_data

        # Process nested relationships on this instance
        if relation.nested:
            self._process_nested_relationships(
                row=row,
                relation=relation,
                instance=instance,
                parent_id=parent_id,
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
        parent_id: t.Any,
        seen_relations: dict[str, set[str]],
        db: t.Any,
        path: str,
    ) -> None:
        """
        Process all nested relationships for a given instance.

        Args:
            row: The database row containing relationship data
            relation: The parent Relationship object containing nested relationships
            instance: The instance to attach nested data to
            parent_id: ID of the root parent for tracking
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
                parent_id=parent_id,
                seen_relations=seen_relations,
                db=db,
                path=path,
            )

    def collect_or_fail(self, exception: t.Optional[Exception] = None) -> "TypedRows[T_MetaInstance]":
        """
        Call .collect() and raise an error if nothing found.

        Basically unwraps t.Optional type.
        """
        return self.collect() or throw(exception or ValueError("Nothing found!"))

    def __iter__(self) -> t.Generator[T_MetaInstance, None, None]:
        """
        You can start iterating a Query Builder object before calling collect, for ease of use.
        """
        yield from self.collect()

    def __count(self, db: TypeDAL, distinct: t.Optional[bool] = None) -> Query:
        # internal, shared logic between .count and ._count
        model = self.model
        query = self.query
        for key, relation in self.relationships.items():
            if (not relation.condition or relation.join != "inner") and not distinct:
                continue

            other = relation.get_table(db)
            if not distinct:
                # todo: can this lead to other issues?
                other = other.with_alias(f"{key}_{hash(relation)}")
            query &= relation.condition(model, other)

        return query

    def count(self, distinct: t.Optional[bool] = None) -> int:
        """
        Return the amount of rows matching the current query.
        """
        db = self._get_db()
        query = self.__count(db, distinct=distinct)

        return db(query).count(distinct)

    def _count(self, distinct: t.Optional[bool] = None) -> str:
        """
        Return the SQL for .count().
        """
        db = self._get_db()
        query = self.__count(db, distinct=distinct)

        return t.cast(str, db(query)._count(distinct))

    def exists(self) -> bool:
        """
        Determines if t.Any records exist matching the current query.

        Returns True if one or more records exist; otherwise, False.

        Returns:
            bool: A boolean indicating whether t.Any records exist.
        """
        return bool(self.count())

    def __paginate(
        self,
        limit: int,
        page: int = 1,
    ) -> "QueryBuilder[T_MetaInstance]":
        available = self.count()

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

    def chunk(self, chunk_size: int) -> t.Generator["TypedRows[T_MetaInstance]", t.Any, None]:
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
        page = 1

        while rows := self.__paginate(chunk_size, page).collect():
            yield rows
            page += 1

    def first(self, verbose: bool = False) -> T_MetaInstance | None:
        """
        Get the first row matching the currently built query.

        Also adds paginate, since it would be a waste to select more rows than needed.
        """
        if row := self.paginate(page=1, limit=1, verbose=verbose).first():
            return self.model.from_row(row)
        else:
            return None

    def _first(self) -> str:
        return self._paginate(page=1, limit=1)

    def first_or_fail(self, exception: t.Optional[BaseException] = None, verbose: bool = False) -> T_MetaInstance:
        """
        Call .first() and raise an error if nothing found.

        Basically unwraps t.Optional type.
        """
        return self.first(verbose=verbose) or throw(exception or ValueError("Nothing found!"))


# note: these imports exist at the bottom of this file to prevent circular import issues:

from .caching import (  # noqa: E402
    create_and_hash_cache_key,
    get_expire,
    load_from_cache,
    save_to_cache,
)
from .relationships import Relationship  # noqa: E402
from .rows import PaginatedRows, TypedRows  # noqa: E402
