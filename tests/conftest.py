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
def dal_psql(psql):
    conn_str = postgres.get_connection_url()
    uri = "postgres://" + conn_str.split("://")[-1]
    with tempfile.TemporaryDirectory() as d:
        yield TypeDAL(uri, attempts=1, migrate=True, enable_typedal_caching=False, folder=d)
