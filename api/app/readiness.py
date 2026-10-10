from functools import cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


@cache
def expected_head():
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[1] / "migrations")
    )
    return ScriptDirectory.from_config(config).get_current_head()
