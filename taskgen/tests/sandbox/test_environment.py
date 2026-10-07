"""Описание окружения для кандидата собирается из реестра и файла зависимостей пакета."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from taskgen.sandbox.environment import describe_runtime
from taskgen.sandbox.errors import SandboxError
from taskgen.sandbox.registry import PACKAGES_DIR, RuntimeRegistry

ML_RECIPE = PACKAGES_DIR / "python-ml" / "3.12.0"


def _pins(path: Path) -> list[tuple[str, str]]:
    """Пины файла зависимостей прочитаны тестом независимо от кода сервиса."""
    pins = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith(("#", "-")):
            name, version = line.split("==")
            pins.append((name, version.split("+")[0]))
    return pins


def test_plain_python_has_only_standard_library():
    text = describe_runtime("python")
    assert text.startswith("Окружение: ")
    assert "Python 3.12.0" in text
    assert "только стандартная библиотека" in text


def test_cpp_names_compiler_and_standard_from_registry():
    spec = RuntimeRegistry.load().get("cpp")
    text = describe_runtime("cpp")
    assert f"GCC {spec.version}" in text
    assert spec.standard is not None and spec.standard.upper() in text.upper()
    assert "только стандартная библиотека" in text


def test_python_ml_lists_libraries_from_requirements():
    text = describe_runtime("python-ml")
    pins = _pins(ML_RECIPE / "requirements.txt")
    assert pins
    for name, version in pins:
        assert f"{name} {version}" in text
    assert "только стандартная библиотека" not in text
    # Локальная метка сборки (+cpu) — деталь установки, а не версия библиотеки для кандидата.
    assert "+cpu" not in text


def test_changed_pin_changes_description(tmp_path):
    packages = tmp_path / "packages"
    shutil.copytree(PACKAGES_DIR, packages)
    requirements = packages / "python-ml" / "3.12.0" / "requirements.txt"
    name, version = _pins(requirements)[-1]
    requirements.write_text(
        re.sub(
            rf"^{re.escape(name)}==.*$",
            f"{name}==99.0.0",
            requirements.read_text(encoding="utf-8"),
            flags=re.MULTILINE,
        ),
        encoding="utf-8",
    )
    registry = RuntimeRegistry.load(packages_dir=packages)
    text = describe_runtime("python-ml", registry)
    assert f"{name} 99.0.0" in text
    assert f"{name} {version}" not in text


def test_unpinned_requirement_is_rejected(tmp_path):
    packages = tmp_path / "packages"
    shutil.copytree(PACKAGES_DIR, packages)
    (packages / "python-ml" / "3.12.0" / "requirements.txt").write_text(
        "torch>=2\n", encoding="utf-8"
    )
    with pytest.raises(SandboxError, match="torch"):
        describe_runtime("python-ml", RuntimeRegistry.load(packages_dir=packages))


def test_build_script_has_no_pins_of_its_own():
    # Пины живут только в requirements.txt: второй список в build.sh разошёлся бы с описанием.
    build = (ML_RECIPE / "build.sh").read_text(encoding="utf-8")
    assert "requirements.txt" in build
    for name, _ in _pins(ML_RECIPE / "requirements.txt"):
        assert f"{name}==" not in build


def test_bash_names_utilities_instead_of_libraries():
    text = describe_runtime("bash")
    assert text.startswith("Окружение: Bash 5.2.0;")
    assert "mawk" in text and "coreutils" in text
    assert "стандартная библиотека" not in text


def test_service_tool_runtime_is_not_offered_to_candidates():
    from taskgen.sandbox.environment import describe_all

    described = describe_all()
    assert "analyzers" not in described
    assert {"python", "python-ml", "cpp", "bash"} <= set(described)


def test_unknown_runtime_is_sandbox_error():
    with pytest.raises(SandboxError, match="rust"):
        describe_runtime("rust")
