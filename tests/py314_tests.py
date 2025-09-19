# filename doesn't start with test_ so pytest on older python versions doesn't import it automatically.
from src.typedal import TypeDAL

def test_sql_expression_314(database: TypeDAL):
    start_date = "2025-01-01"
    expr1 = database.sql_expression(t"date('now') > {start_date}")

    assert str(expr1) == "date('now') > '2025-01-01'"
