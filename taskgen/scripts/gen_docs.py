"""Справочные разделы документации taskgen, собранные из кода, а не набранные руками.

    uv run python scripts/gen_docs.py            пишет docs/taskgen/generated/
    uv run python scripts/gen_docs.py --out DIR  пишет в DIR
    uv run python scripts/gen_docs.py bundle     собирает docs/taskgen/taskgen.docx, нужен pandoc

Источники: OpenAPI приложения (create_app().openapi(), без БД и сервера), модель Settings,
uv.lock и pyproject.toml, реестр окружений песочницы, docker-compose.yml и Dockerfile, банк
шаблонов seed/templates и вакансии seed/vacancies. Вывод не содержит дат и отсортирован:
tests/test_docs_generated.py сравнивает его с закоммиченной копией.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections import defaultdict
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import yaml

from taskgen.app import api_routes, create_app
from taskgen.auth import route_roles
from taskgen.bank.parser import parse_template
from taskgen.config import Settings
from taskgen.contracts import TemplateSpec
from taskgen.sandbox.environment import describe_runtime
from taskgen.sandbox.registry import RuntimeRegistry

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
DOCS = ROOT / "docs" / "taskgen"
GENERATED = DOCS / "generated"
SEED = PROJECT / "seed"
# Путь от сгенерированного файла до корня репозитория, для ссылок на исходники.
TO_ROOT = "../../.."
BUNDLE = DOCS / "taskgen.docx"
# Оглавление документации: порядок разделов в сборке берётся из его ссылок.
INDEX = DOCS / "README.md"

HEADER = (
    "> [!NOTE]\n"
    "> Файл собран командой `uv run python scripts/gen_docs.py` из каталога `taskgen/`. "
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


def span(values: list[int]) -> str:
    low, high = min(values), max(values)
    return str(low) if low == high else f"{low}–{high}"


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
        lines, inline = [], []
        for media, content in body.get("content", {}).items():
            schema = content.get("schema", {})
            if "properties" not in schema:
                lines.append(f"- `{media}`: {schema_type(schema, _schema_link)}")
                continue
            # Схема, заданная в openapi_extra, не попадает в components: её поля — здесь.
            name = schema.get("title") or f"Поля {media}"
            lines.append(f"- `{media}`: {_schema_link(name)}")
            inline.append(_schema_section(name, schema, level="####"))
            inline += [
                _schema_section(sub, spec, level="####")
                for sub, spec in schema.get("$defs", {}).items()
            ]
        required = "обязательно" if body.get("required") else "необязательно"
        parts.append(f"Тело запроса, {required}:\n\n" + "\n".join(lines))
        parts += inline
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
        "`detail` FastAPI: строка или объект с полями, которые называет описание ответа"
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
        "API сервиса taskgen",
        f"OpenAPI приложения `create_app()` из [`app.py`]({TO_ROOT}/taskgen/src/taskgen/app.py) "
        "и роли из `require()` в "
        f"[`auth.py`]({TO_ROOT}/taskgen/src/taskgen/auth.py)",
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
            "у настроек нет description в taskgen/src/taskgen/config.py: " + ", ".join(missing)
        )
    return document(
        "Настройки сервиса taskgen",
        f"модель `Settings` в [`config.py`]({TO_ROOT}/taskgen/src/taskgen/config.py)",
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


def _images() -> list[list[str]]:
    rows = []
    compose = yaml.safe_load((PROJECT / "docker-compose.yml").read_text(encoding="utf-8"))
    for service, spec in sorted(compose["services"].items()):
        if "image" not in spec:
            continue
        image, _, tag = spec["image"].partition(":")
        rows.append([code(image), code(tag) if tag else "тег не задан, `latest`", service])
    dockerfile = (PROJECT / "Dockerfile").read_text(encoding="utf-8")
    for match in re.finditer(r"^(?:FROM|COPY --from=)(\S+)", dockerfile, re.MULTILINE):
        image, _, tag = match.group(1).removeprefix("FROM ").partition(":")
        rows.append([code(image), code(tag) if tag else "тег не задан, `latest`", "образ taskgen"])
    if not rows:
        raise SystemExit("в docker-compose.yml и Dockerfile не нашлось ни одного образа")
    return rows


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
    python = (PROJECT / ".python-version").read_text(encoding="utf-8").strip()
    registry = RuntimeRegistry.load()
    sandbox_rows = []
    library_rows = []
    for name in registry.names():
        spec = registry.get(name)
        source = "официальный Piston" if spec.source == "official" else "собирается локально"
        sandbox_rows.append(
            [
                code(name),
                code(spec.package_name),
                code(spec.version),
                source,
                cell(describe_runtime(name)),
            ]
        )
        library_rows += [
            [code(name), code(lib), code(ver)] for lib, ver in registry.libraries(name)
        ]

    sections = [
        f"Python сервиса — `{python}` (`.python-version`), проект требует "
        f"`{lock['requires-python']}`. Версии ниже — те, что зафиксированы в `uv.lock`; "
        "`uv sync --frozen` ставит ровно их",
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
        "## Окружения песочницы\n\n"
        "Окружения, в которых исполняются генераторы тестов, эталоны и решения кандидатов. "
        "Описание — тот же текст, который видит кандидат в задании\n\n"
        + table(["Окружение", "Пакет Piston", "Версия", "Источник", "Описание"], sandbox_rows),
        "## Библиотеки окружений песочницы\n\n"
        + (
            table(["Окружение", "Библиотека", "Версия"], library_rows)
            if library_rows
            else "Ни одно окружение не ставит библиотек сверх стандартной"
        ),
        "## Контейнеры\n\n" + table(["Образ", "Тег", "Где"], _images()),
    ]
    return document(
        "Библиотеки и компоненты taskgen",
        f"[`uv.lock`]({TO_ROOT}/taskgen/uv.lock), реестр окружений "
        f"[`runtimes.yaml`]({TO_ROOT}/taskgen/src/taskgen/sandbox/runtimes.yaml) с файлами "
        f"зависимостей пакетов в [`sandbox/packages`]({TO_ROOT}/taskgen/sandbox/packages), "
        f"[`docker-compose.yml`]({TO_ROOT}/taskgen/docker-compose.yml) и "
        f"[`Dockerfile`]({TO_ROOT}/taskgen/Dockerfile)",
        *sections,
    )


# --- templates.md -------------------------------------------------------------------------


def _frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
    if match is None:
        raise SystemExit(f"{path}: нет frontmatter")
    return yaml.safe_load(match.group(1))


def render_templates() -> str:
    root = SEED / "templates"
    specs: list[tuple[Path, TemplateSpec]] = [
        (path, parse_template(path.read_text(encoding="utf-8"), source=str(path)))
        for path in sorted(root.rglob("*.md"))
    ]
    if not specs:
        raise SystemExit(f"в {root} нет шаблонов")
    specs.sort(key=lambda item: (item[1].area, item[1].grade, item[1].name))

    cells: dict[tuple[str, str], list[TemplateSpec]] = defaultdict(list)
    for _, spec in specs:
        cells[spec.area, spec.grade].append(spec)
    summary = table(
        ["Область", "Грейд", "С кодом", "Эссе", "Задачки", "Сложность", "Время, мин"],
        [
            [
                code(area),
                code(grade),
                sum(s.kind == "code" for s in group),
                sum(s.kind == "essay" for s in group),
                sum(s.kind == "quiz" for s in group),
                span([s.difficulty for s in group]),
                span([s.expected_minutes for s in group]),
            ]
            for (area, grade), group in sorted(cells.items())
        ],
    )

    sections = [
        f"В банке {len(specs)} шаблонов. Формат шаблона описан в "
        f"[`TEMPLATE_FORMAT.md`]({TO_ROOT}/taskgen/TEMPLATE_FORMAT.md)",
        "## Сводка по ячейкам\n\n" + summary,
    ]
    for area in sorted({spec.area for _, spec in specs}):
        rows = [
            [
                f"[{code(spec.name)}]({TO_ROOT}/{path.relative_to(ROOT).as_posix()})",
                code(spec.grade),
                f"{spec.kind}, {spec.io}" if spec.io else spec.kind,
                code(spec.runtime) if spec.runtime else "",
                spec.difficulty,
                spec.expected_minutes,
                ", ".join(code(skill) for skill in spec.skills),
                len(spec.variants) if spec.kind == "quiz" else len(spec.rubric),
                cell(spec.description),
            ]
            for path, spec in specs
            if spec.area == area
        ]
        sections.append(
            f"## Область `{area}`\n\n"
            + table(
                [
                    "Шаблон",
                    "Грейд",
                    "Тип и вид io",
                    "Окружение",
                    "Сложность",
                    "Время, мин",
                    "Навыки",
                    "Пунктов рубрики или вариантов кейса",
                    "Описание",
                ],
                rows,
            )
        )

    vacancies = sorted((SEED / "vacancies").glob("*.md"))
    vacancy_rows = []
    for path in vacancies:
        meta = _frontmatter(path)
        vacancy_rows.append(
            [
                f"[{cell(meta['title'])}]({TO_ROOT}/{path.relative_to(ROOT).as_posix()})",
                code(meta["area"]),
                code(meta["grade"]),
                ", ".join(code(skill) for skill in meta["skills"]),
            ]
        )
    sections.append(
        "## Демонстрационные вакансии\n\n"
        "Вакансия называет область, грейд и навыки кодами справочников, поэтому её можно "
        "подать в отбор шаблонов полями area, claimed_grade и skills запроса `POST /sessions`\n\n"
        + table(["Вакансия", "Область", "Грейд", "Навыки"], vacancy_rows)
    )
    return document(
        "Банк шаблонов taskgen",
        f"шаблоны [`seed/templates`]({TO_ROOT}/taskgen/seed/templates), разобранные "
        "`parse_template`, и вакансии "
        f"[`seed/vacancies`]({TO_ROOT}/taskgen/seed/vacancies)",
        *sections,
    )


# --- Запись и сборка ----------------------------------------------------------------------


def render_all() -> dict[str, str]:
    spec = openapi_spec()
    return {
        "api.md": render_api(spec),
        "openapi.json": json.dumps(spec, ensure_ascii=False, indent=2) + "\n",
        "settings.md": render_settings(),
        "libraries.md": render_libraries(),
        "templates.md": render_templates(),
    }


def write(out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, text in render_all().items():
        path = out / name
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def bundle_sources() -> list[Path]:
    """Разделы по порядку ссылок оглавления docs/taskgen/README.md, только из docs/taskgen."""
    links = re.findall(r"\]\(([^)#]+\.md)\)", INDEX.read_text(encoding="utf-8"))
    sources = [(DOCS / link).resolve() for link in links]
    sources = [path for path in sources if path.is_relative_to(DOCS) and path != INDEX]
    if not sources or any(not path.is_file() for path in sources):
        raise SystemExit(f"в {INDEX} нет ссылок на разделы или раздела нет: {links}")
    return list(dict.fromkeys(sources))


_MERMAID = re.compile(r"^```mermaid\n(.*?)^```\n", re.MULTILINE | re.DOTALL)
_ADMONITION = re.compile(r"^> \[!(NOTE|IMPORTANT|WARNING|TIP|CAUTION)\]$", re.MULTILINE)
_ADMONITION_TITLE = {
    "NOTE": "Примечание",
    "TIP": "Совет",
    "IMPORTANT": "Важно",
    "WARNING": "Внимание",
    "CAUTION": "Осторожно",
}


def _for_pandoc(text: str, stem: str, work: Path, mmdc: str | None) -> str:
    """GitHub-разметка, которую pandoc не понимает: диаграммы mermaid и плашки > [!NOTE]."""
    text = _ADMONITION.sub(lambda m: f"> **{_ADMONITION_TITLE[m.group(1)]}**\n>", text)
    if mmdc is None:
        return text
    count = 0

    def render(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        source, image = work / f"{stem}-{count}.mmd", work / f"{stem}-{count}.png"
        source.write_text(match.group(1), encoding="utf-8")
        subprocess.run(
            [mmdc, "--quiet", "-i", str(source), "-o", str(image), "-b", "white", "-s", "2"],
            check=True,
        )
        return f"![]({image.name})\n"

    return _MERMAID.sub(render, text)


def bundle(output: Path) -> None:
    pandoc = shutil.which("pandoc")
    if pandoc is None:
        raise SystemExit("нет pandoc; например: nix shell nixpkgs#pandoc -c " + " ".join(sys.argv))
    mmdc = shutil.which("mmdc")
    if mmdc is None:
        print("нет mmdc: диаграммы mermaid попадут в файл исходным текстом", file=sys.stderr)
    with tempfile.TemporaryDirectory(prefix="taskgen-docs-") as directory:
        work = Path(directory)
        inputs = []
        for path in bundle_sources():
            stem = path.relative_to(DOCS).with_suffix("").as_posix().replace("/", "-")
            text = _for_pandoc(path.read_text(encoding="utf-8"), stem, work, mmdc)
            inputs.append(work / f"{stem}.md")
            inputs[-1].write_text(text, encoding="utf-8")
        command = [
            pandoc,
            "--from=gfm",
            "--toc",
            "--metadata=title:Сервис taskgen: сопроводительная документация",
            "--metadata=lang:ru",
            f"--resource-path={work}",
            f"--output={output.resolve()}",
            *map(str, inputs),
        ]
        subprocess.run(command, check=True)
    print(output)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command")
    parser.add_argument("--out", type=Path, default=GENERATED, help="каталог для md-файлов")
    build = sub.add_parser("bundle", help="собрать один .docx или .pdf через pandoc")
    build.add_argument("--output", type=Path, default=BUNDLE, help="файл результата")
    args = parser.parse_args(argv)
    if args.command == "bundle":
        bundle(args.output)
        return 0
    for path in write(args.out):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
