#!/bin/bash
# Пакет Piston python-ml: интерпретатор из официального пакета python 3.12.0 и библиотеки ML.
#
# Как собирается интерпретатор — sandbox/packages/python-base.sh. Скрипт пишет результат в
# свой каталог, как требует формат пакетов Piston.
set -euo pipefail

cd "$(realpath "$(dirname "$0")")"
# shellcheck source-path=SCRIPTDIR source=../../python-base.sh
source ./python-base.sh
# Пины и индексы — только в requirements.txt: по нему же собирается описание окружения.
install_python_with_requirements
