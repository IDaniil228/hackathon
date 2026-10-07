# shellcheck shell=bash
# Общая часть сборки локальных пакетов Piston на Python: python-ml и analyzers.
# Needs bash 3.2 and POSIX tools only, plus python3 and sha256sum of the Piston image
#
# Файл подключается из build.sh пакета через source; piston-packages.sh кладёт его рядом с
# рецептом перед сборкой. Штатный build.sh пакета python собирает CPython из исходников и
# требует образа сборщика Piston с dev-библиотеками. Здесь берётся уже собранный официальный
# пакет: интерпретатор тот же, что в окружении python, а сборка идёт без компилятора. CPython
# находит свой prefix по расположению бинарника, поэтому перенос в другой каталог его не ломает.
#
# Сборка идёт внутри образа Piston, где есть python3 системы, но нет curl.

PYTHON_BASE_URL=https://github.com/engineer-man/piston/releases/download/pkgs/python-3.12.0.pkg.tar.gz
# Сумма из индекса пакетов Piston, тот же файл ставит POST /api/v2/packages.
PYTHON_BASE_SHA256=abc40b3231fc7e713799da2cd79844545c72b3904a4d2ffcc28c4d133ed21d0b

# Кладёт интерпретатор официального пакета python в текущий каталог и ставит в него
# зависимости из requirements.txt; пины и индексы записаны только там.
install_python_with_requirements() {
  python3 -c 'import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])' \
    "$PYTHON_BASE_URL" base.tar.gz
  printf '%s  base.tar.gz\n' "$PYTHON_BASE_SHA256" | sha256sum -c -
  # Только интерпретатор: служебные файлы официального пакета заменили бы файлы этого.
  tar xzf base.tar.gz ./bin ./lib ./include ./share
  rm base.tar.gz
  bin/python3.12 -m pip install --no-cache-dir --disable-pip-version-check \
    -r requirements.txt
}
