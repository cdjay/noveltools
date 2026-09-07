"""Use a fresh temporary database, never the user's data."""
import os
from tempfile import TemporaryDirectory
from pathlib import Path

_test_workspace = TemporaryDirectory(prefix="noveltools-tests-")
os.environ["DATABASE_URL"] = "sqlite:///" + (Path(_test_workspace.name) / "tests.db").as_posix()
os.environ["DATA_DIR"] = _test_workspace.name


def pytest_sessionfinish(session, exitstatus):
    from app.database import engine
    engine.dispose()
    _test_workspace.cleanup()
