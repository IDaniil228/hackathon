"""Поддельный Piston: ответы той формы, которую Piston вернул на реальных запусках."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx

from taskgen.config import SandboxSettings
from taskgen.sandbox.piston import PistonRunner


def stage(**overrides):
    base = {
        "signal": None,
        "stdout": "",
        "stderr": "",
        "code": 0,
        "output": "",
        "memory": 3_784_000,
        "message": None,
        "status": None,
        "cpu_time": 10,
        "wall_time": 18,
    }
    return base | overrides


def execute_response(run: dict, compile_stage: dict | None = None) -> httpx.Response:
    body = {"run": run, "language": "python", "version": "3.12.0"}
    if compile_stage is not None:
        body["compile"] = compile_stage
    return httpx.Response(200, json=body)


def fake_runner(
    answer: Callable[[dict], httpx.Response], sent: list[dict] | None = None
) -> PistonRunner:
    """Runner, у которого вместо Piston функция от тела запроса execute."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/execute"
        body = json.loads(request.content)
        if sent is not None:
            sent.append(body)
        return answer(body)

    http = httpx.AsyncClient(base_url="http://piston.test", transport=httpx.MockTransport(handler))
    return PistonRunner(SandboxSettings(piston_url="http://piston.test"), http=http)
