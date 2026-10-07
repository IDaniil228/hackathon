import pytest

from taskgen.sandbox.errors import SandboxError
from taskgen.sandbox.registry import RuntimeRegistry


def _load(tmp_path, name: str, fields: str) -> RuntimeRegistry:
    path = tmp_path / "runtimes.yaml"
    path.write_text(
        f"{name}:\n  version: 1.0.0\n  description: {name} {{version}}\n{fields}",
        encoding="utf-8",
    )
    return RuntimeRegistry.load(path)


def test_default_registry_knows_service_runtimes():
    registry = RuntimeRegistry.load()

    assert registry.get("python").language == "python"
    # Окружение называет язык своего кода и виды задач явно: python-ml пишется на python.
    shown = {
        name: (spec.code, spec.io, spec.function_driver)
        for name in ("python", "python-ml", "cpp", "bash")
        if (spec := registry.get(name))
    }
    assert shown == {
        "python": ("python", ["stdio", "function"], "python"),
        "python-ml": ("python", ["stdio", "function"], "python"),
        "cpp": ("cpp", ["stdio", "function"], "cpp"),
        "bash": ("bash", ["stdio"], None),
    }


def test_new_runtime_is_added_by_data_only(tmp_path):
    fields = (
        "  language: python\n  code: python\n  io: [stdio, function]\n"
        "  function_driver: python\n  source_file: solution.py\n"
    )
    spec = _load(tmp_path, "python-old", fields).get("python-old")

    assert (spec.code, spec.io, spec.function_driver) == ("python", ["stdio", "function"], "python")


def test_stdio_only_runtime_needs_no_driver(tmp_path):
    fields = "  language: ruby\n  code: ruby\n  io: [stdio]\n  source_file: solution.rb\n"
    spec = _load(tmp_path, "ruby", fields).get("ruby")

    assert spec.io == ["stdio"] and spec.function_driver is None


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        # Язык кода не выводится из имени пакета: python-data без code — ошибка.
        ("  language: python-data\n  io: [stdio]\n  source_file: solution.py\n", "code"),
        # Окружению, в котором решают задания, нужен хотя бы один вид io.
        ("  language: python\n  code: python\n  source_file: solution.py\n", r"\bio\b"),
        (
            "  language: ruby\n  code: ruby\n  io: [stdio, function]\n  source_file: s.rb\n",
            "function_driver",
        ),
        (
            "  language: ruby\n  code: ruby\n  io: [stdio, function]\n"
            "  function_driver: ruby\n  source_file: s.rb\n",
            "ruby",
        ),
        (
            "  language: ruby\n  code: ruby\n  io: [stdio]\n  function_driver: python\n"
            "  source_file: s.rb\n",
            "function_driver",
        ),
    ],
    ids=["no-code", "no-io", "function-no-driver", "unknown-driver", "driver-no-function"],
)
def test_registry_rejects_inconsistent_runtime(tmp_path, fields, reason):
    with pytest.raises(SandboxError, match=reason):
        _load(tmp_path, "custom", fields)


def test_packages_name_what_to_install_in_piston():
    packages = RuntimeRegistry.load().packages()

    assert ("official", "python", "3.12.0") in packages
    # Язык c++ Piston получает из пакета gcc.
    assert ("official", "gcc", "10.2.0") in packages
    assert ("local", "python-ml", "3.12.0") in packages
    assert len(packages) == len(set(packages))


def test_registry_module_prints_packages_for_the_installer():
    import subprocess
    import sys

    printed = subprocess.run(
        [sys.executable, "-m", "taskgen.sandbox.registry"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    assert "official gcc 10.2.0\n" in printed
    assert "local python-ml 3.12.0\n" in printed


def test_unknown_runtime_is_a_sandbox_error():
    with pytest.raises(SandboxError, match="rust"):
        RuntimeRegistry.load().get("rust")


def test_registry_rejects_a_field_it_does_not_know(tmp_path):
    # Устаревшее поле driver иначе молча не действовало бы.
    path = tmp_path / "runtimes.yaml"
    path.write_text(
        "go:\n  language: go\n  version: 1.16.2\n  code: go\n  io: [stdio]\n  driver: go\n"
        "  source_file: main.go\n  description: Go {version}\n",
        encoding="utf-8",
    )

    with pytest.raises(SandboxError, match="driver"):
        RuntimeRegistry.load(path)
