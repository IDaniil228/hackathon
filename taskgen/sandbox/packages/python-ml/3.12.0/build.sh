#!/bin/bash
# Пакет Piston python-ml: интерпретатор из официального пакета python 3.12.0 и библиотеки ML.
#
# Штатный build.sh пакета python собирает CPython из исходников и требует образа сборщика
# Piston с dev-библиотеками. Здесь берётся уже собранный официальный пакет: интерпретатор
# тот же, что в окружении python, а сборка идёт без компилятора. CPython находит свой prefix
# по расположению бинарника, поэтому перенос в каталог python-ml его не ломает.
#
# Скрипт запускается внутри образа Piston, где есть python3 системы, но нет curl, и пишет
# результат в свой каталог, как требует формат пакетов Piston.
set -euo pipefail

PREFIX=$(realpath "$(dirname "$0")")
BASE_URL=https://github.com/engineer-man/piston/releases/download/pkgs/python-3.12.0.pkg.tar.gz
# Сумма из индекса пакетов Piston, тот же файл ставит POST /api/v2/packages.
BASE_SHA256=abc40b3231fc7e713799da2cd79844545c72b3904a4d2ffcc28c4d133ed21d0b

cd "$PREFIX"
python3 -c 'import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])' \
  "$BASE_URL" base.tar.gz
printf '%s  base.tar.gz\n' "$BASE_SHA256" | sha256sum -c -
# Только интерпретатор: служебные файлы официального пакета заменили бы файлы этого.
tar xzf base.tar.gz ./bin ./lib ./include ./share
rm base.tar.gz

# Пины и индексы — только в requirements.txt: по нему же собирается описание окружения.
bin/python3.12 -m pip install --no-cache-dir --disable-pip-version-check \
  -r requirements.txt
