"""Изоляция запусков в реальном Piston: сеть, время, процессы, память, файловая система.

Каждый тест проверяет, что граница держит, а не то, что код кандидата упал: если изоляцию
ослабить, например поднять лимит или открыть сеть, тест должен покраснеть.
"""

import time

import pytest

from taskgen.contracts import RunLimits, RunRequest

pytestmark = pytest.mark.integration

TIMEOUT_MS = 1500
MEMORY = 128 * 1024 * 1024
LIMITS = RunLimits(timeout_ms=TIMEOUT_MS, memory_bytes=MEMORY)


def python(code: str, limits: RunLimits = LIMITS) -> RunRequest:
    return RunRequest(runtime="python", files={"main.py": code}, limits=limits)


async def test_network_is_unreachable(piston):
    code = (
        "import socket\n"
        "for host, port in [('1.1.1.1', 80), ('8.8.8.8', 53), ('172.17.0.1', 2000)]:\n"
        "    try:\n"
        "        socket.create_connection((host, port), timeout=1).close()\n"
        "        print('connected', host)\n"
        "    except OSError as error:\n"
        "        print('blocked', host, error.errno)\n"
        "try:\n"
        "    socket.getaddrinfo('example.com', 80)\n"
        "    print('resolved')\n"
        "except OSError:\n"
        "    print('no dns')\n"
    )

    result = await piston.run(python(code))

    assert result.exit_code == 0, result.stderr
    assert "connected" not in result.stdout
    assert "resolved" not in result.stdout
    assert result.stdout.count("blocked") == 3


async def test_infinite_loop_is_cut_at_the_time_limit(piston):
    started = time.monotonic()

    result = await piston.run(python("while True:\n    pass\n"))

    assert result.timed_out
    assert result.wall_ms <= TIMEOUT_MS + 500
    assert time.monotonic() - started < TIMEOUT_MS / 1000 + 5


async def test_sleep_is_cut_by_wall_clock(piston):
    result = await piston.run(python("import time\ntime.sleep(30)\n"))

    assert result.timed_out
    assert result.wall_ms <= TIMEOUT_MS + 500


async def test_fork_bomb_is_contained(piston):
    started = time.monotonic()
    code = (
        "import os\nwhile True:\n    try:\n        os.fork()\n    except OSError:\n        pass\n"
    )

    result = await piston.run(python(code))

    assert result.exit_code != 0 or result.signal is not None
    assert result.signal == "SIGKILL" or result.timed_out
    assert time.monotonic() - started < TIMEOUT_MS / 1000 + 5
    # Песочница пережила бомбу и принимает следующий запуск.
    after = await piston.run(python("print('alive')"))
    assert after.stdout == "alive\n"


async def test_process_count_is_limited(piston):
    code = (
        "import os, time\n"
        "children = 0\n"
        "try:\n"
        "    for _ in range(300):\n"
        "        if os.fork() == 0:\n"
        "            time.sleep(5)\n"
        "            os._exit(0)\n"
        "        children += 1\n"
        "except OSError:\n"
        "    pass\n"
        "print(children, flush=True)\n"
        "os._exit(0)\n"
    )
    # Памяти с запасом, чтобы упереться именно в лимит процессов, а не в OOM killer.
    roomy = RunLimits(timeout_ms=3000, memory_bytes=1024 * 1024 * 1024)

    result = await piston.run(python(code, roomy))

    assert result.stdout.strip().isdigit(), result.stderr
    assert int(result.stdout) < 300


async def test_memory_limit_kills_the_process(piston):
    code = (
        "chunks = []\n"
        "for _ in range(64):\n"
        "    chunks.append(bytearray(16 * 1024 * 1024))\n"
        "print('survived')\n"
    )

    result = await piston.run(python(code))

    assert "survived" not in result.stdout
    assert result.signal == "SIGKILL"
    assert result.memory_bytes <= MEMORY * 1.05


async def test_host_processes_are_invisible(piston):
    code = "import os\nprint(sum(name.isdigit() for name in os.listdir('/proc')))\n"

    result = await piston.run(python(code))

    assert int(result.stdout) < 5


async def test_writes_do_not_survive_into_the_next_run(piston):
    write = (
        "import os\n"
        "targets = ['/tmp/taskgen-marker', '/box/taskgen-marker', 'taskgen-marker',\n"
        "           '/piston/packages/python/3.12.0/taskgen-marker', '/etc/taskgen-marker']\n"
        "for path in targets:\n"
        "    try:\n"
        "        with open(path, 'w') as f:\n"
        "            f.write('x')\n"
        "        print('wrote', path)\n"
        "    except OSError:\n"
        "        print('denied', path)\n"
    )
    check = (
        "import os\n"
        "for path in ['/tmp/taskgen-marker', '/box/taskgen-marker', 'taskgen-marker',\n"
        "             '/piston/packages/python/3.12.0/taskgen-marker', '/etc/taskgen-marker']:\n"
        "    print(path, os.path.exists(path))\n"
    )

    first = await piston.run(python(write))
    second = await piston.run(python(check))

    # Пакет окружения и /etc смонтированы только для чтения.
    assert "denied /piston/packages/python/3.12.0/taskgen-marker" in first.stdout
    assert "denied /etc/taskgen-marker" in first.stdout
    assert second.exit_code == 0, second.stderr
    assert "True" not in second.stdout
    assert second.stdout.count("False") == 5
