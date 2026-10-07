import pytest

from taskgen.sandbox.errors import SandboxError
from taskgen.sandbox.registry import RuntimeRegistry


def test_default_registry_knows_service_runtimes():
    registry = RuntimeRegistry.load()

    assert registry.get("python").language == "python"
    # Окружение называет язык своего кода явно: python-ml пишется на python.
    assert {name: registry.get(name).code for name in ("python", "python-ml", "cpp", "bash")} == {
        "python": "python",
        "python-ml": "python",
        "cpp": "cpp",
        "bash": "bash",
    }
    assert registry.get("python-ml").functions and registry.get("cpp").functions
    assert not registry.get("bash").functions


def test_new_runtime_is_added_by_data_only(tmp_path):
    path = tmp_path / "runtimes.yaml"
    path.write_text(
        "python-old:\n  language: python\n  version: 3.10.0\n  code: python\n"
        "  source_file: solution.py\n  description: Python {version}\n",
        encoding="utf-8",
    )

    spec = RuntimeRegistry.load(path).get("python-old")

    assert (spec.language, spec.version, spec.code, spec.functions) == (
        "python",
        "3.10.0",
        "python",
        True,
    )


def test_runtime_on_a_language_without_driver_is_stdio_only(tmp_path):
    path = tmp_path / "runtimes.yaml"
    path.write_text(
        "ruby:\n  language: ruby\n  version: 3.0.1\n  code: ruby\n  source_file: solution.rb\n"
        "  description: Ruby {version}\n",
        encoding="utf-8",
    )

    assert not RuntimeRegistry.load(path).get("ruby").functions


def test_runtime_must_name_the_language_of_its_code(tmp_path):
    path = tmp_path / "runtimes.yaml"
    path.write_text(
        "python-data:\n  language: python-data\n  version: 3.12.0\n"
        "  source_file: solution.py\n  description: Python {version}\n",
        encoding="utf-8",
    )

    with pytest.raises(SandboxError, match="python-data"):
        RuntimeRegistry.load(path)


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
        "go:\n  language: go\n  version: 1.16.2\n  code: go\n  driver: go\n"
        "  source_file: main.go\n  description: Go {version}\n",
        encoding="utf-8",
    )

    with pytest.raises(SandboxError, match="go"):
        RuntimeRegistry.load(path)
