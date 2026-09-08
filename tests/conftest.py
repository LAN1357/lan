import pytest

from src.workbench.closing import close_month
from src.workbench.db import connect
from tests.test_workbench_closing import seed


@pytest.fixture
def closed_db(tmp_path):
    path = tmp_path / 'workbench.db'
    conn = connect(path)
    seed(conn)
    close_month(conn, '2026-09', confirmed=True)
    yield path, conn
    conn.close()

