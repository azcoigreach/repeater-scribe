from __future__ import annotations

import importlib
import os
import tempfile
import warnings
from pathlib import Path


def _import_starlette_testclient():
    # Starlette 1.6.0 evaluates this deprecated AnyIO 4.15 alias at import time.
    # Remove this workaround once Starlette uses anyio.from_thread.BlockingPortal.
    # Keep the exception local to this import; all other deprecations stay visible.
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=(
                r"^The anyio\.abc\.BlockingPortal alias is deprecated, "
                r"use anyio\.from_thread\.BlockingPortal instead\.$"
            ),
            category=DeprecationWarning,
            module=r"^starlette\.testclient$",
        )
        importlib.import_module("starlette.testclient")


_import_starlette_testclient()

_test_data = Path(tempfile.mkdtemp(prefix="repeater-scribe-tests-"))
os.environ["ASLT_DATABASE_URL"] = f"sqlite:///{_test_data / 'tests.db'}"
os.environ["ASLT_DATA_DIR"] = str(_test_data)
os.environ["ASLT_TMP_DIR"] = str(_test_data / "tmp")
os.environ["ASLT_ARCHIVE_PATHS"] = str(_test_data / "archive")
os.environ["ASLT_DEPLOYMENT_MODE"] = "local"
os.environ["ASLT_AUTH_MODE"] = "off"
os.environ["ASLT_ALLOWED_HOSTS"] = "localhost,127.0.0.1,testserver"
os.environ["ASLT_AMI_ENABLED"] = "false"
os.environ["ASLT_AMI_CONTROL_ENABLED"] = "false"
os.environ["ASLT_AMI_RAW_FUNCTION_ENABLED"] = "false"
os.environ["ASLT_FAVORITE_STATS_ENABLED"] = "false"
os.environ["ASLT_AUTO_PROCESS"] = "false"
os.environ["ASLT_LIVE_TRANSCRIPTION"] = "false"

from sqlalchemy import text

import asl_transcriber.models  # noqa: F401
from asl_transcriber.database import Base, engine

Base.metadata.create_all(engine)
with engine.begin() as connection:
	connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
	connection.execute(text("INSERT INTO alembic_version (version_num) VALUES ('events_sessions')"))
