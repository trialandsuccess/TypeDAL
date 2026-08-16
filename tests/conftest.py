import tempfile

import pytest
from testcontainers.postgres import PostgresContainer

from src.typedal import TypeDAL

postgres = PostgresContainer(
    dbname="postgres",
    username="someuser",
    password="somepass",
)


@pytest.fixture(scope="module", autouse=True)
def psql(request):
    postgres.ports = {
        5432: 9631,  # as set in valid.env
    }

    request.addfinalizer(postgres.stop)
    postgres.start()


@pytest.fixture
def dal_psql_uri(psql) -> str:
    conn_str = postgres.get_connection_url()
    return "postgres://" + conn_str.split("://")[-1]


@pytest.fixture
def dal_psql(dal_psql_uri: str):
    # function-scoped, so this runs once per test - which makes closing it mandatory rather
    # than tidy. Without the close each test leaves a Postgres connection open (idle in
    # transaction, since migrate=True runs DDL on it), and the container's default ceiling of
    # 100 is reached partway through the suite: everything from then on fails to connect with
    # `FATAL: sorry, too many clients already`.
    with tempfile.TemporaryDirectory() as d:
        db = TypeDAL(dal_psql_uri, attempts=1, migrate=True, enable_typedal_caching=False, folder=d)
        try:
            yield db
        finally:
            db.close()
