"""Справочные разделы документации matcher, собранные из кода, а не набранные руками.

    uv run python scripts/gen_docs.py            пишет docs/matcher/generated/
    uv run python scripts/gen_docs.py --out DIR  пишет в DIR

Источники: OpenAPI приложения (create_app().openapi(), без БД и сервера), модель Settings,
uv.lock и pyproject.toml, docker-compose.yml с подключённым db/docker-compose.yml и
Dockerfile. Вывод не содержит дат и отсортирован: tests/test_docs_generated.py сравнивает
его с закоммиченной копией. Помощники разбора схем повторяют taskgen/scripts/gen_docs.py:
пакеты сервисов не импортируют друг друга.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

from fastapi.routing import APIRoute
from starlette.routing import BaseRoute

from matcher.app import create_app
from matcher.config import Settings

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
GENERATED = ROOT / "docs" / "matcher" / "generated"
# Путь от сгенерированного файла до корня репозитория, для ссылок на исходники.
TO_ROOT = "../../.."

HEADER = (
    "> [!NOTE]\n"
    "> Файл собран командой `uv run python scripts/gen_docs.py` из каталога `matcher/`. "
    "Не правьте его руками: правьте источник и пересоберите файл. Источник — "
)

# Ответы, которые FastAPI описывает сам и по-английски.
_FASTAPI_TEXT = {
    "Successful Response": "Успешный ответ",
    "Validation Error": "Тело или параметры запроса не прошли проверку схемы",
}


# --- Общие части --------------------------------------------------------------------------


def prose(text: str | None) -> str:
    """Текст в одну строку без точки в конце: так требуют правила прозы проекта."""
    if not text:
        return ""
    joined = " ".join(text.split())
    return joined[:-1] if joined.endswith(".") and not joined.endswith("..") else joined


def cell(text: Any) -> str:
    return prose(str(text)).replace("|", "\\|") if text not in (None, "") else ""


def code(text: Any) -> str:
    value = str(text)
    fence = "``" if "`" in value else "`"
    pad = " " if value.startswith("`") or value.endswith("`") else ""
    return f"{fence}{pad}{value}{pad}{fence}".replace("|", "\\|")


def table(head: list[str], rows: Iterable[list[Any]]) -> str:
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(str(item) for item in row) + " |" for row in rows]
    return "\n".join(lines)


def anchor(name: str) -> str:
    """Якорь заголовка так, как его строит GitHub."""
    return re.sub(r"[^\w\- ]", "", name.lower()).replace(" ", "-")


def document(title: str, source: str, *blocks: str) -> str:
    parts = [f"# {title}", HEADER + source, *blocks]
    return "\n\n".join(part.strip("\n") for part in parts if part) + "\n"


# --- Типы из JSON Schema ------------------------------------------------------------------


def schema_type(schema: dict[str, Any], link: Callable[[str], str]) -> str:
    """Тип поля JSON Schema одной строкой; link превращает имя схемы в ссылку."""
    if "$ref" in schema:
        return link(schema["$ref"].rsplit("/", 1)[-1])
    if "anyOf" in schema:
        return " или ".join(schema_type(option, link) for option in schema["anyOf"])
    if "enum" in schema:
        return "одно из " + ", ".join(
            code(json.dumps(v, ensure_ascii=False)) for v in schema["enum"]
        )
    if "const" in schema:
        return code(json.dumps(schema["const"], ensure_ascii=False))
    kind = schema.get("type")
    if kind == "array":
        return f"array из {schema_type(schema.get('items', {}), link)}"
    if kind is None:
        return "любой JSON"
    text = kind
    if "format" in schema:
        text += f" ({schema['format']})"
    limits = [
        f"{sign} {schema[key]}"
        for key, sign in (
            ("minimum", "≥"),
            ("exclusiveMinimum", ">"),
            ("maximum", "≤"),
            ("exclusiveMaximum", "<"),
            ("minLength", "длина ≥"),
            ("maxLength", "длина ≤"),
            ("minItems", "элементов ≥"),
            ("maxItems", "элементов ≤"),
        )
        if key in schema
    ]
    if "pattern" in schema:
        limits.append(f"шаблон {code(schema['pattern'])}")
    return text + (", " + ", ".join(limits) if limits else "")


def default_value(schema: dict[str, Any]) -> str:
    if "default" not in schema:
        return ""
    return code(json.dumps(schema["default"], ensure_ascii=False))


# --- api.md -------------------------------------------------------------------------------


def openapi_spec() -> dict[str, Any]:
    return create_app(Settings()).openapi()


def api_routes(routes: Iterable[BaseRoute]) -> Iterator[APIRoute]:
    """Все эндпоинты приложения, включая эндпоинты подключённых роутеров."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        elif (included := getattr(route, "original_router", None)) is not None:
            yield from api_routes(included.routes)


def route_roles(route: APIRoute) -> tuple[Any, ...] | None:
    """Роли, которые пропускает эндпоинт; None — эндпоинт без ключа.

    Ищет проверку require() среди зависимостей эндпоинта и его роутера на любой глубине.
    """
    pending = [route.dependant]
    while pending:
        dependant = pending.pop()
        roles = getattr(dependant.call, "allowed_roles", None)
        if roles is not None:
            return roles
        pending.extend(dependant.dependencies)
    return None


def _route_roles() -> dict[tuple[str, str], str]:
    """(метод, путь) → роли эндпоинта; эндпоинт без require() — без ключа."""
    roles: dict[tuple[str, str], str] = {}
    for route in api_routes(create_app(Settings()).routes):
        allowed = route_roles(route)
        text = " или ".join(code(role.value) for role in allowed) if allowed else "без ключа"
        for method in route.methods:
            roles[method.lower(), route.path] = text
    return roles


def _schema_link(name: str) -> str:
    return f"[{name}](#{anchor(name)})"


def _operation(method: str, path: str, op: dict[str, Any], role: str) -> str:
    parts = [f"### `{method.upper()} {path}`", f"**{prose(op.get('summary'))}**"]
    if op.get("description"):
        parts.append(prose(op["description"]))
    parts.append(f"Роль: {role}")
    params = op.get("parameters", [])
    if params:
        parts.append(
            "Параметры:\n\n"
            + table(
                ["Имя", "Где", "Обязательный", "Тип", "Описание"],
                [
                    [
                        code(p["name"]),
                        p["in"],
                        "да" if p.get("required") else "нет",
                        schema_type(p.get("schema", {}), _schema_link),
                        cell(p.get("description") or p.get("schema", {}).get("description")),
                    ]
                    for p in params
                ],
            )
        )
    body = op.get("requestBody")
    if body:
        lines = [
            f"- `{media}`: {schema_type(content.get('schema', {}), _schema_link)}"
            for media, content in body.get("content", {}).items()
        ]
        required = "обязательно" if body.get("required") else "необязательно"
        parts.append(f"Тело запроса, {required}:\n\n" + "\n".join(lines))
    rows = []
    for status, response in sorted(op.get("responses", {}).items()):
        schemas = [
            schema_type(content["schema"], _schema_link)
            for content in response.get("content", {}).values()
            if content.get("schema")
        ]
        text = response.get("description", "")
        rows.append([code(status), cell(_FASTAPI_TEXT.get(text, text)), ", ".join(schemas)])
    parts.append("Ответы:\n\n" + table(["Код", "Описание", "Тело"], rows))
    return "\n\n".join(parts)


def _schema_section(name: str, schema: dict[str, Any], level: str = "###") -> str:
    parts = [f"{level} {name}"]
    if schema.get("description"):
        parts.append(prose(schema["description"]))
    if "enum" in schema:
        parts.append("Значения: " + ", ".join(code(value) for value in schema["enum"]))
    properties = schema.get("properties", {})
    if properties:
        required = set(schema.get("required", []))
        parts.append(
            table(
                ["Поле", "Тип", "Обязательное", "По умолчанию", "Описание"],
                [
                    [
                        code(field),
                        schema_type(spec, _schema_link),
                        "да" if field in required else "нет",
                        default_value(spec),
                        cell(spec.get("description")),
                    ]
                    for field, spec in properties.items()
                ],
            )
        )
    return "\n\n".join(parts)


def render_api(spec: dict[str, Any]) -> str:
    roles = _route_roles()
    documented = {
        (method, path)
        for path, ops in spec["paths"].items()
        for method in ops
        if method != "parameters"
    }
    if not documented or documented != roles.keys():
        raise SystemExit(f"эндпоинты OpenAPI и приложения расходятся: {documented ^ roles.keys()}")

    by_tag: dict[str, list[str]] = defaultdict(list)
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            tag = (op.get("tags") or ["Без тега"])[0]
            by_tag[tag].append(_operation(method, path, op, roles[method, path]))

    info = spec["info"]
    intro = (
        f"{prose(info.get('summary'))}. Спецификация OpenAPI {spec['openapi']} целиком лежит "
        "рядом, в [openapi.json](openapi.json); запущенный сервис отдаёт её по `/openapi.json`, "
        "а Swagger UI — по `/docs`\n\n"
        "Ключ передаётся заголовком `Authorization: Bearer <ключ>`. Роль `admin` проходит "
        "любую проверку роли, поэтому стоит в каждом списке ролей. Формат ошибки — объект "
        "`detail` FastAPI: строка или список ошибок проверки схемы"
    )
    sections = [intro]
    for tag, operations in by_tag.items():
        sections.append(f"## {tag}\n\n" + "\n\n".join(operations))
    schemas = spec.get("components", {}).get("schemas", {})
    sections.append(
        "## Схемы\n\n"
        + "\n\n".join(_schema_section(name, schema) for name, schema in schemas.items())
    )
    return document(
        "API сервиса matcher",
        f"OpenAPI приложения `create_app()` из [`app.py`]({TO_ROOT}/matcher/src/matcher/app.py) "
        "и роли из `require()` в "
        f"[`auth.py`]({TO_ROOT}/matcher/src/matcher/auth.py)",
        *sections,
    )


# --- settings.md --------------------------------------------------------------------------


def render_settings() -> str:
    schema = Settings.model_json_schema()
    defs = schema.get("$defs", {})
    config = Settings.model_config
    prefix, delimiter = config["env_prefix"], config["env_nested_delimiter"]
    missing: list[str] = []

    def ref(schema: dict[str, Any]) -> dict[str, Any] | None:
        return defs[schema["$ref"].rsplit("/", 1)[-1]] if "$ref" in schema else None

    def rows(
        properties: dict[str, Any], path: list[str], defaults: dict[str, Any] | None = None
    ) -> list[list[str]]:
        """Строки полей; вложенная модель и словарь моделей раскрываются до листьев.

        defaults — значение по умолчанию объемлющего поля: у вложенной модели оно задаёт
        умолчания её полей вместо умолчаний самого класса.
        """
        result = []
        for name, field in properties.items():
            dotted = ".".join([*path, name])
            if not field.get("description"):
                missing.append(dotted)
            if defaults is not None and name in defaults:
                default = code(json.dumps(defaults[name], ensure_ascii=False))
            else:
                default = default_value(field)
            nested = ref(field)
            items = ref(field.get("additionalProperties") or {})
            result.append(
                [
                    code(dotted),
                    code(prefix + delimiter.join([*path, name]).upper()),
                    schema_type(field, code)
                    if items is None
                    else f"словарь {code('имя')} → объект",
                    "" if nested or items else default,
                    cell(field.get("description")),
                ]
            )
            if nested is not None:
                result += rows(nested["properties"], [*path, name], field.get("default"))
            elif items is not None:
                for key, value in field.get("default", {}).items():
                    result += rows(items["properties"], [*path, name, key], value)
        return result

    head = ["Поле", "Переменная окружения", "Тип", "По умолчанию", "Описание"]
    scalars = {k: v for k, v in schema["properties"].items() if "$ref" not in v}
    groups = {k: v for k, v in schema["properties"].items() if "$ref" in v}
    sections = [
        f"Каждое поле задаётся переменной окружения из таблицы или строкой в файле "
        f"`{config['env_file']}` в рабочем каталоге процесса. Вложенное поле отделяется "
        f"`{delimiter}`, регистр имени переменной не важен",
        "## Общие\n\n" + table(head, rows(scalars, [])),
    ]
    for name, field in groups.items():
        group = defs[field["$ref"].rsplit("/", 1)[-1]]
        intro = prose(field.get("description") or group.get("description"))
        if not intro:
            missing.append(name)
        sections.append(
            f"## {name}\n\n{intro}\n\n" + table(head, rows(group["properties"], [name]))
        )
    if missing:
        raise SystemExit(
            "у настроек нет description в matcher/src/matcher/config.py: " + ", ".join(missing)
        )
    return document(
        "Настройки сервиса matcher",
        f"модель `Settings` в [`config.py`]({TO_ROOT}/matcher/src/matcher/config.py) и общая "
        "часть настроек LLM в "
        f"[`llm_config.py`]({TO_ROOT}/matcher/src/matcher/llm/llm_config.py), копии taskgen",
        *sections,
    )


# --- libraries.md -------------------------------------------------------------------------


def _lock_packages(lock: dict[str, Any]) -> tuple[dict[str, Any], dict[str, list[str]]]:
    root = next(p for p in lock["package"] if p.get("source", {}).get("editable") == ".")
    versions: dict[str, list[str]] = defaultdict(list)
    for package in lock["package"]:
        if package is not root:
            versions[package["name"]].append(package["version"])
    return root, versions


def _specifiers(entries: list[dict[str, Any]]) -> dict[str, str]:
    return {e["name"]: e.get("specifier", "") for e in entries}


def _compose_images(path: Path, where: str) -> list[list[str]]:
    """Образы сервисов compose-файла: строки `image:` под ключом верхнего уровня services."""
    rows = []
    in_services, service = False, None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split(" #")[0].rstrip()
        if re.match(r"^\S", line):
            in_services = line.startswith("services:")
            continue
        if in_services and (head := re.match(r"^  ([\w-]+):\s*$", line)):
            service = head.group(1)
        elif in_services and service and (image := re.match(r"^    image:\s*(\S+)$", line)):
            name, _, tag = image.group(1).partition(":")
            tag_text = code(tag) if tag else "тег не задан, `latest`"
            rows.append([code(name), tag_text, f"{service} ({where})"])
    return rows


def _images() -> list[list[str]]:
    rows = _compose_images(PROJECT / "docker-compose.yml", "matcher/docker-compose.yml")
    rows += _compose_images(ROOT / "db" / "docker-compose.yml", "db/docker-compose.yml")
    dockerfile = (PROJECT / "Dockerfile").read_text(encoding="utf-8")
    for match in re.finditer(r"^(?:FROM |COPY --from=)(\S+)", dockerfile, re.MULTILINE):
        image, _, tag = match.group(1).partition(":")
        rows.append([code(image), code(tag) if tag else "тег не задан, `latest`", "образ matcher"])
    if not rows:
        raise SystemExit("в docker-compose.yml и Dockerfile не нашлось ни одного образа")
    return sorted(rows)


def render_libraries() -> str:
    lock = tomllib.loads((PROJECT / "uv.lock").read_text(encoding="utf-8"))
    root, versions = _lock_packages(lock)
    runtime = {d["name"] for d in root.get("dependencies", [])}
    groups = {
        name: {d["name"] for d in group} for name, group in root.get("dev-dependencies", {}).items()
    }
    dev = set().union(*groups.values())
    metadata = root.get("metadata", {})
    constraints = _specifiers(metadata.get("requires-dist", []))
    for group in metadata.get("requires-dev", {}).values():
        constraints |= {k: v for k, v in _specifiers(group).items() if v}
    if not runtime:
        raise SystemExit("в uv.lock нет зависимостей проекта")

    def direct_rows(names: set[str]) -> list[list[str]]:
        return [
            [
                code(name),
                ", ".join(code(v) for v in versions[name]),
                code(constraints[name]) if constraints.get(name) else "любая",
            ]
            for name in sorted(names)
        ]

    transitive = sorted(set(versions) - runtime - dev)
    sections = [
        f"Проект требует Python `{lock['requires-python']}`, образ сервиса задаёт `Dockerfile`. "
        "Версии ниже — те, что зафиксированы в `uv.lock`; `uv sync --frozen` ставит ровно их",
        "## Прямые зависимости сервиса\n\n"
        + table(["Пакет", "Версия", "Ограничение в pyproject.toml"], direct_rows(runtime)),
        "## Зависимости разработки\n\n"
        + table(
            ["Пакет", "Версия", "Ограничение в pyproject.toml"],
            direct_rows(groups.get("dev", set()) - runtime),
        ),
        *(
            f"## Необязательная группа `{name}`\n\nСтавится только явно: "
            f"`uv sync --group {name}`\n\n"
            + table(
                ["Пакет", "Версия", "Ограничение в pyproject.toml"], direct_rows(names - runtime)
            )
            for name, names in sorted(groups.items())
            if name != "dev"
        ),
        "## Транзитивные зависимости\n\nЗависимости всех пакетов выше\n\n"
        + table(
            ["Пакет", "Версия"],
            [[code(n), ", ".join(code(v) for v in versions[n])] for n in transitive],
        ),
        "## Контейнеры\n\n" + table(["Образ", "Тег", "Где"], _images()),
    ]
    return document(
        "Библиотеки и компоненты matcher",
        f"[`uv.lock`]({TO_ROOT}/matcher/uv.lock), "
        f"[`docker-compose.yml`]({TO_ROOT}/matcher/docker-compose.yml) с подключённым "
        f"[`db/docker-compose.yml`]({TO_ROOT}/db/docker-compose.yml) и "
        f"[`Dockerfile`]({TO_ROOT}/matcher/Dockerfile)",
        *sections,
    )


# --- Запись -------------------------------------------------------------------------------


def render_all() -> dict[str, str]:
    spec = openapi_spec()
    return {
        "api.md": render_api(spec),
        "openapi.json": json.dumps(spec, ensure_ascii=False, indent=2) + "\n",
        "settings.md": render_settings(),
        "libraries.md": render_libraries(),
    }


def write(out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, text in render_all().items():
        path = out / name
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=GENERATED, help="каталог для md-файлов")
    args = parser.parse_args(argv)
    for path in write(args.out):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
