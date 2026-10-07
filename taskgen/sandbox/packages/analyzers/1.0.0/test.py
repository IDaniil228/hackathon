import subprocess
import sys

import clang_tidy
import cppcheck

for module in ("ruff", "radon", "lizard"):
    subprocess.run([sys.executable, "-m", module, "--version"], check=True, capture_output=True)
# Скрипты pip в bin/ ссылаются на интерпретатор каталога сборки, поэтому бинарники — из колёс.
for tool in (clang_tidy.get_executable("clang-tidy"), cppcheck.get_cppcheck_dir() / "cppcheck"):
    subprocess.run([tool, "--version"], check=True, capture_output=True)
print("OK")
