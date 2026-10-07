"""Реестр окружений: имя runtime сервиса -> язык и версия пакета Piston.

Данные лежат в runtimes.yaml рядом с модулем, поэтому окружение добавляется без правки кода.
Задачи stdio доступны любому окружению реестра. Код нужен только для драйвера задач-функций
нового языка, и поле driver ограничено языками, для которых драйвер написан.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from taskgen.contracts import RunLimits
from taskgen.sandbox.drivers import FUNCTION_LANGUAGES
from taskgen.sandbox.errors import SandboxError

# Рецепты локальных пакетов Piston: taskgen/sandbox/packages. Каталог лежит вне пакета
# Python, потому что его же читает скрипт установки; сервис запускается из исходников
# (uv sync ставит проект в режиме editable, и образ Docker копирует весь taskgen/).
PACKAGES_DIR = Path(__file__).resolve().parents[3] / "sandbox" / "packages"
REQUIREMENTS_FILE = "requirements.txt"


class RuntimeSpec(BaseModel):
    # Неизвестное поле — ошибка загрузки реестра, а не молча забытая запись.
    model_config = ConfigDict(extra="forbid")

    # Язык пакета Piston: его имя Piston ждёт в запросе запуска.
    language: str
    version: str
    # Язык кода окружения: python у python и python-ml. По нему выбираются драйвер задач-
    # функций, анализаторы, рубрика и проверка решений шаблона.
    code: str
    # language — язык решений кандидата; tool — служебное окружение сервиса, например
    # анализаторы кода: в нём не решают задания, и кандидату оно не описывается.
    kind: Literal["language", "tool"] = "language"
    # Имя файла программы кандидата в задаче stdio; по нему же анализаторы узнают язык.
    source_file: str
    # Можно ли положить рядом с программой файлы кейса. Скрипт compile пакета gcc собирает
    # каждый файл запуска как исходник, поэтому C++ файлов данных не принимает.
    data_files: bool = True
    # Что есть в окружении сверх языка, если это не библиотеки локального пакета, например
    # утилиты командной строки; без поля описание называет библиотеки пакета.
    extras: str | None = None
    # Описание для человека; {version} и {standard} подставляются из полей ниже, чтобы
    # версия была записана один раз.
    description: str
    # Стандарт языка, с которым пакет Piston компилирует решение, например c++17.
    standard: str | None = None
    compile_memory_bytes: int | None = None
    # Пакет Piston, который даёт язык; один пакет может давать несколько языков.
    package: str | None = None
    # official ставится из репозитория Piston, local собирается из taskgen/sandbox/packages.
    source: Literal["official", "local"] = "official"
    # Пол лимитов окружения: тяжёлый рантайм не запускается в лимитах по умолчанию.
    min_timeout_ms: int | None = None
    min_memory_bytes: int | None = None

    @property
    def package_name(self) -> str:
        return self.package or self.language

    @property
    def functions(self) -> bool:
        """Доступны ли задачи io=function: для языка кода написан драйвер."""
        return self.code in FUNCTION_LANGUAGES

    def effective_limits(self, limits: RunLimits) -> RunLimits:
        """Запрошенные лимиты, поднятые до пола окружения; бо́льшие запросы не меняются."""
        return RunLimits(
            timeout_ms=max(limits.timeout_ms, self.min_timeout_ms or 0),
            memory_bytes=max(limits.memory_bytes, self.min_memory_bytes or 0),
        )


class RuntimeRegistry:
    def __init__(self, runtimes: dict[str, RuntimeSpec], packages_dir: Path = PACKAGES_DIR) -> None:
        self._runtimes = runtimes
        self._packages_dir = packages_dir

    @classmethod
    def load(cls, path: Path | None = None, packages_dir: Path = PACKAGES_DIR) -> RuntimeRegistry:
        if path is None:
            text = resources.files(__package__).joinpath("runtimes.yaml").read_text("utf-8")
        else:
            text = path.read_text(encoding="utf-8")
        raw = yaml.safe_load(text) or {}
        runtimes = {}
        for name, fields in raw.items():
            try:
                runtimes[name] = RuntimeSpec.model_validate(fields)
            except ValidationError as error:
                raise SandboxError(f"окружение {name} описано неверно: {error}") from error
        return cls(runtimes, packages_dir)

    def get(self, name: str) -> RuntimeSpec:
        try:
            return self._runtimes[name]
        except KeyError:
            known = ", ".join(sorted(self._runtimes))
            raise SandboxError(f"неизвестное окружение {name}, известны: {known}") from None

    def names(self) -> list[str]:
        return sorted(self._runtimes)

    def packages(self) -> list[tuple[str, str, str]]:
        """Пакеты Piston, нужные окружениям: (source, package, version) без повторов."""
        return sorted(
            {(spec.source, spec.package_name, spec.version) for spec in self._runtimes.values()}
        )

    def libraries(self, name: str) -> list[tuple[str, str]]:
        """Библиотеки окружения сверх стандартной: (имя, версия) из файла зависимостей пакета.

        Официальные пакеты Piston дают только язык. У локального пакета библиотеки — те, что
        его build.sh ставит из requirements.txt; нет файла — нет и библиотек.
        """
        spec = self.get(name)
        if spec.source != "local":
            return []
        path = self._packages_dir / spec.package_name / spec.version / REQUIREMENTS_FILE
        if not path.is_file():
            return []
        pins = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            # Строки с - — опции pip вроде --extra-index-url, а не библиотеки.
            if not line or line.startswith("-"):
                continue
            library, separator, version = line.partition("==")
            if not separator or not version.strip():
                raise SandboxError(
                    f"{path}: зависимость {line!r} без точной версии; описание окружения "
                    "называет кандидату версии, поэтому нужен пин вида имя==версия"
                )
            # Локальная метка (+cpu) говорит, какая сборка ставится, а не какая версия.
            pins.append((library.strip(), version.strip().split("+", 1)[0]))
        return pins


if __name__ == "__main__":
    # Список читает скрипт установки пакетов, поэтому версии заданы только в runtimes.yaml.
    for source, package, version in RuntimeRegistry.load().packages():
        print(source, package, version)
