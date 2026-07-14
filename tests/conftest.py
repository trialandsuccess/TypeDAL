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
    with tempfile.TemporaryDirectory() as d:
        yield TypeDAL(dal_psql_uri, attempts=1, migrate=True, enable_typedal_caching=False, folder=d)
