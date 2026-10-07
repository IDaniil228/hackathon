#!/bin/bash
# Пакет Piston analyzers: интерпретатор из официального пакета python 3.12.0 и инструменты
# статического анализа, которые разбирают код кандидата внутри песочницы, а не в сервисе.
#
# Как собирается интерпретатор — sandbox/packages/python-base.sh. Скрипт пишет результат в
# свой каталог, как требует формат пакетов Piston.
set -euo pipefail

cd "$(realpath "$(dirname "$0")")"
# shellcheck source-path=SCRIPTDIR source=../../python-base.sh
source ./python-base.sh
install_python_with_requirements
