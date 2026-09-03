from __future__ import annotations

import os
import tempfile
from pathlib import Path

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
	connection.execute(text("INSERT INTO alembic_version (version_num) VALUES ('archive_foundation')"))
