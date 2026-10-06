import pytest

from taskgen.config import Settings
from taskgen.sandbox.piston import PistonRunner


@pytest.fixture
async def piston():
    """Клиент к реальному Piston; адрес берётся из TASKGEN_SANDBOX__PISTON_URL."""
    runner = PistonRunner(Settings().sandbox)
    yield runner
    await runner.aclose()
