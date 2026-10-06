"""Обвязка тестов taskgen для Python. Исполняется внутри песочницы, а не в сервисе.

Протокол описан в taskgen/sandbox/protocol.py. Обвязка вызывает функцию решения на каждом
входе и печатает ответ; сравнивает с ожидаемым значением сервис, вне песочницы.
"""

import importlib
import json
import os
import sys
import time
import traceback

# Длинная трассировка не нужна для отчёта и раздувает вывод, а он у Piston ограничен.
ERROR_LIMIT = 2000


def main() -> None:
    entrypoint = sys.argv[1]
    nonce = sys.stdin.readline().strip()
    inputs = json.loads(sys.stdin.readline())

    # Канал протокола — копия исходного stdout. Сам stdout и stdin уводятся в /dev/null,
    # чтобы print и input решения не смешивались с протоколом.
    channel = os.fdopen(os.dup(1), "w", encoding="utf-8")
    null = os.open(os.devnull, os.O_RDWR)
    os.dup2(null, 0)
    os.dup2(null, 1)

    def emit(record: dict) -> None:
        channel.write(f"{nonce} {json.dumps(record, ensure_ascii=False, allow_nan=False)}\n")
        # Сброс после каждой записи сохраняет готовые кейсы, если следующий упрётся в лимит.
        channel.flush()

    sys.path.insert(0, os.getcwd())
    try:
        function = getattr(importlib.import_module("solution"), entrypoint)
    except AttributeError:
        emit({"load_error": f"в решении нет функции {entrypoint}"})
        return
    except BaseException:
        emit({"load_error": _short(traceback.format_exc(limit=-3))})
        return

    for index, args in enumerate(inputs):
        try:
            start = time.perf_counter()
            if isinstance(args, dict):
                value = function(**args)
            else:
                value = function(*args)
            elapsed_ms = (time.perf_counter() - start) * 1000
            emit({"case": index, "ok": True, "value": _plain(value), "ms": elapsed_ms})
        except BaseException as error:
            # BaseException, а не Exception: sys.exit в решении не должен обрывать прогон.
            emit({"case": index, "ok": False, "error": _short(_describe(error))})


def _plain(value):
    """Приводит ответ к JSON: кортеж к списку, массив и скаляр numpy к значениям Python."""
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if hasattr(value, "tolist"):
        return _plain(value.tolist())
    return value


def _describe(error: BaseException) -> str:
    if isinstance(error, ValueError | TypeError) and "JSON" in str(error):
        return f"ответ не переводится в JSON: {error}"
    return "".join(traceback.format_exception_only(error)).strip()


def _short(text: str) -> str:
    return text if len(text) <= ERROR_LIMIT else text[:ERROR_LIMIT] + "…"


if __name__ == "__main__":
    main()
