import pytest

from taskgen.sandbox.errors import SandboxError
from taskgen.sandbox.registry import RuntimeRegistry


def test_default_registry_knows_service_runtimes():
    registry = RuntimeRegistry.load()

    assert registry.get("python").language == "python"
    assert registry.get("python").driver == "python"
    assert registry.get("python-ml").driver == "python"
    assert registry.get("cpp").driver == "cpp"


def test_new_runtime_is_added_by_data_only(tmp_path):
    path = tmp_path / "runtimes.yaml"
    path.write_text(
        "python-old:\n  language: python\n  version: 3.10.0\n  driver: python\n"
        "  source_file: solution.py\n  description: Python {version}\n",
        encoding="utf-8",
    )

    spec = RuntimeRegistry.load(path).get("python-old")

    assert (spec.language, spec.version, spec.driver) == ("python", "3.10.0", "python")


def test_runtime_without_driver_is_stdio_only(tmp_path):
    path = tmp_path / "runtimes.yaml"
    path.write_text(
        "ruby:\n  language: ruby\n  version: 3.0.1\n  source_file: solution.rb\n"
        "  description: Ruby {version}\n",
        encoding="utf-8",
    )

    assert RuntimeRegistry.load(path).get("ruby").driver is None


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


def test_registry_rejects_driver_without_code(tmp_path):
    path = tmp_path / "runtimes.yaml"
    path.write_text("go:\n  language: go\n  version: 1.16.2\n  driver: go\n", encoding="utf-8")

    with pytest.raises(SandboxError, match="go"):
        RuntimeRegistry.load(path)
