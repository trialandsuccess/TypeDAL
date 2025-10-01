"""
Helpers that work independently of core.
"""

from __future__ import annotations

import datetime as dt
import fnmatch
import io
import re
import sys
import types
import typing as t
from collections import ChainMap

from pydal import DAL

from .types import AnyDict, Expression, Field, Row, T, Table, Template  # type: ignore

try:
    import annotationlib
except ImportError:  # pragma: no cover
    annotationlib = None

if t.TYPE_CHECKING:
    from string.templatelib import Interpolation

    from . import TypeDAL, TypedField, TypedTable


def is_union(some_type: type | types.UnionType) -> bool:
    """
    Check if a type is some type of Union.

    Args:
        some_type: types.UnionType = type(int | str); t.Union = t.Union[int, str]

    """
    return t.get_origin(some_type) in (types.UnionType, t.Union)


def reversed_mro(cls: type) -> t.Iterable[type]:
    """
    Get the Method Resolution Order (mro) for a class, in reverse order to be used with ChainMap.
    """
    return reversed(getattr(cls, "__mro__", []))


def _cls_annotations(c: type) -> dict[str, type]:  # pragma: no cover
    """
    Functions to get the annotations of a class (excl inherited, use _all_annotations for that).

    Uses `annotationlib` if available (since 3.14) and if so, resolves forward references immediately.
    """
    if annotationlib:
        return t.cast(
            dict[str, type],
            annotationlib.get_annotations(c, format=annotationlib.Format.VALUE, eval_str=True),
        )
    else:
        return getattr(c, "__annotations__", {})


def _all_annotations(cls: type) -> ChainMap[str, type]:
    """
    Returns a dictionary-like ChainMap that includes annotations for all \
    attributes defined in cls or inherited from superclasses.
    """
    # chainmap reverses the iterable, so reverse again beforehand to keep order normally:

    return ChainMap(*(_cls_annotations(c) for c in reversed_mro(cls)))


def all_dict(cls: type) -> AnyDict:
    """
    Get the internal data of a class and all it's parents.
    """
    return dict(ChainMap(*(c.__dict__ for c in reversed_mro(cls))))  # type: ignore


def all_annotations(cls: type, _except: t.Optional[t.Iterable[str]] = None) -> dict[str, type]:
    """
    Wrapper around `_all_annotations` that filters away t.Any keys in _except.

    It also flattens the ChainMap to a regular dict.
    """
    if _except is None:
        _except = set()

    _all = _all_annotations(cls)
    return {k: v for k, v in _all.items() if k not in _except}


def instanciate(cls: t.Type[T] | T, with_args: bool = False) -> T:
    """
    Create an instance of T (if it is a class).

    If it already is an instance, return it.
    If it is a generic (list[int)) create an instance  of the 'origin' (-> list()).

    If with_args: spread the generic args into the class creation
    (needed for e.g. TypedField(str), but not for list[str])
    """
    if inner_cls := t.get_origin(cls):
        if not with_args:
            return t.cast(T, inner_cls())

        args = t.get_args(cls)
        return t.cast(T, inner_cls(*args))

    if isinstance(cls, type):
        return t.cast(T, cls())

    return cls


def origin_is_subclass(obj: t.Any, _type: type) -> bool:
    """
    Check if the origin of a generic is a subclass of _type.

    Example:
        origin_is_subclass(list[str], list) -> True
    """
    return bool(
        t.get_origin(obj) and isinstance(t.get_origin(obj), type) and issubclass(t.get_origin(obj), _type),
    )


def mktable(
    data: dict[t.Any, t.Any],
    header: t.Optional[t.Iterable[str] | range] = None,
    skip_first: bool = True,
) -> str:
    """
    Display a table for 'data'.

    See Also:
         https://stackoverflow.com/questions/70937491/python-flexible-way-to-format-string-output-into-a-table-without-using-a-non-st
    """
    # get max col width
    col_widths: list[int] = list(map(max, zip(*(map(lambda x: len(str(x)), (k, *v)) for k, v in data.items()))))

    # default numeric header if missing
    if not header:
        header = range(1, len(col_widths) + 1)

    header_widths = map(lambda x: len(str(x)), header)

    # correct column width if headers are longer
    col_widths = [max(c, h) for c, h in zip(col_widths, header_widths)]

    # create separator line
    line = f"+{'+'.join('-' * (w + 2) for w in col_widths)}+"

    # create formating string
    fmt_str = "| %s |" % " | ".join(f"{{:<{i}}}" for i in col_widths)

    output = io.StringIO()
    # header
    print()
    print(line, file=output)
    print(fmt_str.format(*header), file=output)
    print(line, file=output)

    # data
    for k, v in data.items():
        values = list(v.values())[1:] if skip_first else v.values()
        print(fmt_str.format(k, *values), file=output)

    # footer
    print(line, file=output)

    return output.getvalue()


K = t.TypeVar("K")
V = t.TypeVar("V")


def looks_like(v: t.Any, _type: type[t.Any]) -> bool:
    """
    Returns true if v or v's class is of type _type, including if it is a generic.

    Examples:
        assert looks_like([], list)
        assert looks_like(list, list)
        assert looks_like(list[str], list)
    """
    return isinstance(v, _type) or (isinstance(v, type) and issubclass(v, _type)) or origin_is_subclass(v, _type)


def filter_out(mut_dict: dict[K, V], _type: type[T]) -> dict[K, type[T]]:
    """
    Split a dictionary into things matching _type and the rest.

    Modifies mut_dict and returns everything of type _type.
    """
    return {k: mut_dict.pop(k) for k, v in list(mut_dict.items()) if looks_like(v, _type)}


def unwrap_type(_type: type) -> type:
    """
    Get the inner type of a generic.

    Example:
        list[list[str]] -> str
    """
    while args := t.get_args(_type):
        _type = args[0]
    return _type


@t.overload
def extract_type_optional(annotation: T) -> tuple[T, bool]:
    """
    T -> T is not exactly right because you'll get the inner type, but mypy seems happy with this.
    """


@t.overload
def extract_type_optional(annotation: None) -> tuple[None, bool]:
    """
    None leads to None, False.
    """


def extract_type_optional(annotation: T | None) -> tuple[T | None, bool]:
    """
    Given an annotation, extract the actual type and whether it is optional.
    """
    if annotation is None:
        return None, False

    if origin := t.get_origin(annotation):
        args = t.get_args(annotation)

        if origin in (t.Union, types.UnionType, t.Optional) and args:
            # remove None:
            return next(_ for _ in args if _ and _ != types.NoneType and not isinstance(_, types.NoneType)), True

    return annotation, False


def to_snake(camel: str) -> str:
    """
    Convert CamelCase to snake_case.

    See Also:
        https://stackoverflow.com/a/44969381
    """
    return "".join([f"_{c.lower()}" if c.isupper() else c for c in camel]).lstrip("_")


class DummyQuery:
    """
    Placeholder to &= and |= actual query parts.
    """

    def __or__(self, other: T) -> T:
        """
        For 'or': DummyQuery | Other == Other.
        """
        return other

    def __and__(self, other: T) -> T:
        """
        For 'and': DummyQuery & Other == Other.
        """
        return other

    def __bool__(self) -> bool:
        """
        A dummy query is falsey, since it can't actually be used!
        """
        return False


def as_lambda(value: T) -> t.Callable[..., T]:
    """
    Wrap value in a callable.
    """
    return lambda *_, **__: value


def match_strings(patterns: list[str] | str, string_list: list[str]) -> list[str]:
    """
    Glob but on a list of strings.
    """
    if isinstance(patterns, str):
        patterns = [patterns]

    matches = []
    for pattern in patterns:
        matches.extend([s for s in string_list if fnmatch.fnmatch(s, pattern)])

    return matches


def utcnow() -> dt.datetime:
    """
    Replacement of datetime.utcnow.
    """
    # return dt.datetime.now(dt.UTC)
    return dt.datetime.now(dt.timezone.utc)


def get_db(table: "TypedTable | Table") -> "DAL":
    """
    Get the underlying DAL instance for a pydal or typedal table.
    """
    return t.cast("DAL", table._db)


def get_table(table: "TypedTable | Table") -> "Table":
    """
    Get the underlying pydal table for a typedal table.
    """
    return t.cast("Table", table._table)


def get_field(field: "TypedField[t.Any] | Field") -> "Field":
    """
    Get the underlying pydal field from a typedal field.
    """
    return t.cast(
        "Field",
        field,  # Table.field already is a Field, but cast to make sure the editor knows this too.
    )


class classproperty:
    """
    Combination of @classmethod and @property.
    """

    def __init__(self, fget: t.Callable[..., t.Any]) -> None:
        """
        Initialize the classproperty.

        Args:
            fget: A function that takes the class as an argument and returns a value.
        """
        self.fget = fget

    def __get__(self, obj: t.Any, owner: t.Type[T]) -> t.Any:
        """
        Retrieve the property value.

        Args:
            obj: The instance of the class (unused).
            owner: The class that owns the property.

        Returns:
            The value returned by the function.
        """
        return self.fget(owner)


def smarter_adapt(db: TypeDAL, placeholder: t.Any) -> str:
    """
    Smarter adaptation of placeholder to quote if needed.

    Args:
        db: Database object.
        placeholder: Placeholder object.

    Returns:
        Quoted placeholder if needed, except for numbers (smart_adapt logic)
            or fields/tables (use already quoted rname).
    """
    return t.cast(
        str,
        getattr(placeholder, "sql_shortref", None)  # for tables
        or getattr(placeholder, "sqlsafe", None)  # for fields
        or db._adapter.smart_adapt(placeholder),  # for others
    )


# https://docs.python.org/3.14/library/string.templatelib.html
SYSTEM_SUPPORTS_TEMPLATES = sys.version_info > (3, 14)


def process_tstring(template: Template, operation: t.Callable[["Interpolation"], str]) -> str:  # pragma: no cover
    """
    Process a Template string by applying an operation to each interpolation.

    This function iterates through a Template object, which contains both string literals
    and Interpolation objects. String literals are preserved as-is, while Interpolation
    objects are transformed using the provided operation function.

    Args:
        template: A Template object containing mixed string literals and Interpolation objects.
        operation: A callable that takes an Interpolation object and returns a string.
                  This function will be applied to each interpolated value in the template.

    Returns:
        str: The processed string with all interpolations replaced by the results of
             applying the operation function.

    Example:
        Basic f-string functionality can be implemented as:

        >>> def fstring_operation(interpolation):
        ...     return str(interpolation.value)
        >>> value = "test"
        >>> template = t"{value = }"  # Template string literal
        >>> result = process_tstring(template, fstring_operation)
        >>> print(result)  # "value = test"

    Note:
        This is a generic template processor. The specific behavior depends entirely
        on the operation function provided.
    """
    return "".join(part if isinstance(part, str) else operation(part) for part in template)


def sql_escape_template(db: TypeDAL, sql_fragment: Template) -> str:  # pragma: no cover
    r"""
    Safely escape a Template string for SQL execution using database-specific escaping.

    This function processes a Template string (t-string) by escaping all interpolated
    values using the database adapter's escape mechanism, preventing SQL injection
    attacks while maintaining the structure of the SQL query.

    Args:
        db: TypeDAL database connection object that provides the adapter for escaping.
        sql_fragment: A Template object (t-string) containing SQL with interpolated values.
                     The interpolated values will be automatically escaped.

    Returns:
        str: SQL string with all interpolated values properly escaped for safe execution.

    Example:
        >>> user_input = "'; DROP TABLE users; --"
        >>> query = t"SELECT * FROM users WHERE name = {user_input}"
        >>> safe_query = sql_escape_template(db, query)
        >>> print(safe_query)  # "SELECT * FROM users WHERE name = '\'; DROP TABLE users; --'"

    Security:
        This function is essential for preventing SQL injection attacks when using
        user-provided data in SQL queries. All interpolated values are escaped
        according to the database adapter's rules.

    Note:
        Only available in Python 3.14+ when SYSTEM_SUPPORTS_TEMPLATES is True.
        For earlier Python versions, use sql_escape() with string formatting.
    """
    return process_tstring(sql_fragment, lambda part: smarter_adapt(db, part.value))


def sql_escape(db: TypeDAL, sql_fragment: str | Template, *raw_args: t.Any, **raw_kwargs: t.Any) -> str:
    """
    Generate escaped SQL fragments with safely substituted placeholders.

    This function provides secure SQL string construction by escaping all provided
    arguments using the database adapter's escaping mechanism. It supports both
    traditional string formatting (Python < 3.14) and Template strings (Python 3.14+).

    Args:
        db: TypeDAL database connection object that provides the adapter for escaping.
        sql_fragment: SQL fragment with placeholders (%s for positional, %(name)s for named).
                     In Python 3.14+, this can also be a Template (t-string) with
                     interpolated values that will be automatically escaped.
        *raw_args: Positional arguments to be escaped and substituted for %s placeholders.
                  Only use with string fragments, not Template objects.
        **raw_kwargs: Keyword arguments to be escaped and substituted for %(name)s placeholders.
                     Only use with string fragments, not Template objects.

    Returns:
        str: SQL fragment with all placeholders replaced by properly escaped values.

    Raises:
        ValueError: If both positional and keyword arguments are provided simultaneously.

    Examples:
        Positional arguments:
        >>> safe_sql = sql_escape(db, "SELECT * FROM users WHERE id = %s", user_id)

        Keyword arguments:
        >>> safe_sql = sql_escape(db, "SELECT * FROM users WHERE name = %(name)s", name=username)

        Template strings (Python 3.14+):
        >>> safe_sql = sql_escape(db, t"SELECT * FROM users WHERE id = {user_id}")

    Security:
        All arguments are escaped using the database adapter's escaping rules to prevent
        SQL injection attacks. Never concatenate user input directly into SQL strings.
    """
    if raw_args and raw_kwargs:  # pragma: no cover
        raise ValueError("Please provide either args or kwargs, not both.")

    if SYSTEM_SUPPORTS_TEMPLATES and isinstance(sql_fragment, Template):  # pragma: no cover
        return sql_escape_template(db, sql_fragment)

    if raw_args:
        # list
        return sql_fragment % tuple(smarter_adapt(db, placeholder) for placeholder in raw_args)
    else:
        # dict
        return sql_fragment % {key: smarter_adapt(db, placeholder) for key, placeholder in raw_kwargs.items()}


def sql_expression(
    db: TypeDAL,
    sql_fragment: str | Template,
    *raw_args: t.Any,
    output_type: str | None = None,
    **raw_kwargs: t.Any,
) -> Expression:
    """
    Create a PyDAL Expression object from a raw SQL fragment with safe parameter substitution.

    This function combines SQL escaping with PyDAL's Expression system, allowing you to
    create database expressions from raw SQL while maintaining security through proper
    parameter escaping.

    Args:
        db: The TypeDAL database connection object.
        sql_fragment: Raw SQL fragment with placeholders (%s for positional, %(name)s for named).
                     In Python 3.14+, this can also be a Template (t-string) with
                     interpolated values that will be automatically escaped.
        *raw_args: Positional arguments to be escaped and interpolated into the SQL fragment.
                  Only use with string fragments, not Template objects.
        output_type: Optional type hint for the expected output type of the expression.
                    This can help with query analysis and optimization.
        **raw_kwargs: Keyword arguments to be escaped and interpolated into the SQL fragment.
                     Only use with string fragments, not Template objects.

    Returns:
        Expression: A PyDAL Expression object wrapping the safely escaped SQL fragment.

    Examples:
        Creating a complex WHERE clause:
        >>> expr = sql_expression(db,
        ...     "age > %s AND status = %s",
        ...     18, "active",
        ...     output_type="boolean")
        >>> query = db(expr).select()

        Using keyword arguments:
        >>> expr = sql_expression(db,
        ...     "EXTRACT(year FROM %(date_col)s) = %(year)s",
        ...     date_col="created_at", year=2023,
        ...     output_type="boolean")

        Template strings (Python 3.14+):
        >>> min_age = 21
        >>> expr = sql_expression(db, t"age >= {min_age}", output_type="boolean")

    Security:
        All parameters are escaped using sql_escape() before being wrapped in the Expression,
        ensuring protection against SQL injection attacks.

    Note:
        The returned Expression can be used anywhere PyDAL expects an expression,
        such as in db().select(), .update(), or .delete() operations.
    """
    safe_sql = sql_escape(db, sql_fragment, *raw_args, **raw_kwargs)

    # create a pydal Expression wrapping a raw SQL fragment + placeholders
    return Expression(
        db,
        db._adapter.dialect.raw,
        safe_sql,
        type=output_type,  # optional type hint
    )


def normalize_table_keys(row: Row, pattern: re.Pattern[str] = re.compile(r"^([a-zA-Z_]+)_(\d{5,})$")) -> Row:
    """
    Normalize table keys in a PyDAL Row object by stripping numeric hash suffixes from table names, \
    only if the suffix is 5 or more digits.

    For example:
        Row({'articles_12345': {...}}) -> Row({'articles': {...}})
        Row({'articles_123': {...}})   -> unchanged

    Returns:
        Row: A new Row object with normalized keys.
    """
    new_data: dict[str, t.Any] = {}
    for key, value in row.items():
        if match := pattern.match(key):
            base, _suffix = match.groups()
            normalized_key = base
            new_data[normalized_key] = value
        else:
            new_data[key] = value
    return Row(new_data)


def default_representer(field: TypedField[T], value: T, table: t.Type[TypedTable]) -> str:
    """
    Simply call field.represent on the value.
    """
    if represent := getattr(field, "represent", None):
        return str(represent(value, table))
    else:
        return repr(value)


def throw(exc: BaseException) -> t.Never:
    """
    Raise the given exception.

    This function provides a functional way to raise exceptions, allowing
    exception raising to be used in expressions where a statement wouldn't work.

    Args:
        exc: The exception to be raised.

    Returns:
        Never returns normally as an exception is always raised.

    Raises:
        BaseException: Always raises the provided exception.

    Examples:
        >>> value = get_value() or throw(ValueError("No value available"))
        >>> result = data.get('key') if data else throw(KeyError("Missing data"))
    """
    raise exc
