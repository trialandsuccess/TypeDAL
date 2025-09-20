import sqlite3

import pytest

from src.typedal import TypeDAL
from src.typedal.helpers import process_tstring, sql_escape, sql_escape_template, sql_expression


def test_process_tstring_basic(database: TypeDAL):
    """Test the basic f-string functionality example from process_tstring docstring."""

    def fstring_operation(interpolation):
        return str(interpolation.value)

    value = "test"
    template = t"{value = }"
    result = process_tstring(template, fstring_operation)
    assert result == "value = test"


def test_sql_escape_template_security(database: TypeDAL):
    """Test the SQL injection prevention example from sql_escape_template docstring."""
    user_input = "'; DROP TABLE users; --"
    query = t"SELECT * FROM users WHERE name = {user_input}"
    safe_query = sql_escape_template(database, query)

    # The exact escaping format depends on the database adapter, but it should be escaped
    assert "DROP TABLE users" in safe_query  # The dangerous part should still be there
    assert safe_query != f"SELECT * FROM users WHERE name = {user_input}"  # But it should be escaped
    # For most SQL adapters, strings are wrapped in quotes and escaped
    assert "'" in safe_query or '"' in safe_query  # Should have some form of quoting


def test_sql_escape_positional_example(database: TypeDAL):
    """Test the positional arguments example from sql_escape docstring."""
    user_id = 123
    safe_sql = sql_escape(database, "SELECT * FROM users WHERE id = %s", user_id)
    assert safe_sql == "SELECT * FROM users WHERE id = 123"


def test_sql_escape_keyword_example(database: TypeDAL):
    """Test the keyword arguments example from sql_escape docstring."""
    username = "john_doe"
    safe_sql = sql_escape(database, "SELECT * FROM users WHERE name = %(name)s", name=username)
    assert safe_sql == "SELECT * FROM users WHERE name = 'john_doe'"


def test_sql_escape_template_example(database: TypeDAL):
    """Test the Template string example from sql_escape docstring."""
    user_id = 456
    safe_sql = sql_escape(database, t"SELECT * FROM users WHERE id = {user_id}")
    assert safe_sql == "SELECT * FROM users WHERE id = 456"


def test_sql_expression_complex_where(database: TypeDAL):
    """Test the complex WHERE clause example from sql_expression docstring."""
    expr = sql_expression(database, "age > %s AND status = %s", 18, "active", output_type="boolean")

    expected = "age > 18 AND status = 'active'"
    assert str(expr) == expected
    assert expr.type == "boolean"


def test_sql_expression_keyword_extract(database: TypeDAL):
    """Test the keyword arguments EXTRACT example from sql_expression docstring."""
    expr = sql_expression(
        database, "EXTRACT(year FROM %(date_col)s) = %(year)s", date_col="created_at", year=2023, output_type="boolean"
    )

    expected = "EXTRACT(year FROM 'created_at') = 2023"
    assert str(expr) == expected
    assert expr.type == "boolean"


def test_sql_expression_template_age(database: TypeDAL):
    """Test the Template string age example from sql_expression docstring."""
    min_age = 21
    expr = sql_expression(database, t"age >= {min_age}", output_type="boolean")

    expected = "age >= 21"
    assert str(expr) == expected
    assert expr.type == "boolean"


def test_date_expression_similar_to_other_test(database: TypeDAL):
    start_date = "2025-01-01"
    expr1 = database.sql_expression(t"date('now') > {start_date}")
    assert str(expr1) == "date('now') > '2025-01-01'"


def test_executesql_without_tstring(database: TypeDAL):
    bobby_tables = "Robert'); DROP TABLE Students;--"

    database.executesql(f"""
    CREATE TABLE hackable (
        name VARCHAR(100)
    )
    """)

    with pytest.raises(sqlite3.OperationalError):
        database.executesql(f"INSERT INTO hackable(name) VALUES ({bobby_tables})")

    with pytest.raises(sqlite3.OperationalError):
        database.executesql(f"SELECT * FROM hackable where name = {bobby_tables}")


def test_executesql_with_tstring(database: TypeDAL):
    bobby_tables = "Robert'); DROP TABLE Students;--"

    database.executesql(t"""
    CREATE TABLE unhackable (
        name VARCHAR(100)
    )
    """)

    database.executesql(t"INSERT INTO unhackable(name) VALUES ({bobby_tables})")

    rows = database.executesql(t"SELECT * FROM unhackable where name = {bobby_tables}")

    assert len(rows) == 1
    assert rows[0][0] == bobby_tables

    # alternative using magic:
    name = bobby_tables
    rows = database.executesql(t"SELECT * FROM unhackable where {name = }")

    assert len(rows) == 1
    assert rows[0][0] == bobby_tables


def test_sql_expression_314(database: TypeDAL):
    """Main test function that calls all example tests to verify docstring examples."""
    # Call all the docstring example tests
    test_process_tstring_basic(database)
    test_sql_escape_template_security(database)
    test_sql_escape_positional_example(database)
    test_sql_escape_keyword_example(database)
    test_sql_escape_template_example(database)
    test_sql_expression_complex_where(database)
    test_sql_expression_keyword_extract(database)
    test_sql_expression_template_age(database)
    # + the one similar to the non-tstring test:
    test_date_expression_similar_to_other_test(database)
    # executesql with string:
    test_executesql_without_tstring(database)
    test_executesql_with_tstring(database)
