import os
import pathlib
import shutil

_db = pathlib.Path(__file__).parent / "test_abyat.db"
if _db.exists():
    _db.unlink()

_reports = pathlib.Path(__file__).parent / "test_reports"
shutil.rmtree(_reports, ignore_errors=True)

os.environ["DATABASE_URL"] = f"sqlite:///{_db}"
os.environ["REPORTS_DIR"] = str(_reports)
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["SECRET_KEY"] = "test-secret-key-test-secret-key-0123456789"
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "admin-pass-123"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c
