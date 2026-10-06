import pytest

from taskgen.sandbox.errors import SandboxError
from taskgen.sandbox.registry import RuntimeRegistry


def test_default_registry_knows_service_runtimes():
    registry = RuntimeRegistry.load()

    assert registry.get("python").language == "python"
    assert registry.get("python").harness == "python"
    assert registry.get("python-ml").harness == "python"
    assert registry.get("cpp").harness == "cpp"


def test_new_runtime_is_added_by_data_only(tmp_path):
    path = tmp_path / "runtimes.yaml"
    path.write_text(
        "python-old:\n  language: python\n  version: 3.10.0\n  harness: python\n",
        encoding="utf-8",
    )

    spec = RuntimeRegistry.load(path).get("python-old")

    assert (spec.language, spec.version, spec.harness) == ("python", "3.10.0", "python")


def test_unknown_runtime_is_a_sandbox_error():
    with pytest.raises(SandboxError, match="rust"):
        RuntimeRegistry.load().get("rust")


def test_registry_rejects_harness_without_code(tmp_path):
    path = tmp_path / "runtimes.yaml"
    path.write_text("go:\n  language: go\n  version: 1.16.2\n  harness: go\n", encoding="utf-8")

    with pytest.raises(SandboxError, match="go"):
        RuntimeRegistry.load(path)
