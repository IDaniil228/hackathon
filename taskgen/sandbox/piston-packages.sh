#!/usr/bin/env bash
# Needs bash 3.2 and POSIX tools only, plus curl, docker and uv
#
# Ставит в Piston пакеты, которые нужны окружениям taskgen. Список пакетов и их версии
# скрипт берёт из реестра окружений (src/taskgen/sandbox/runtimes.yaml) через
# python -m taskgen.sandbox.registry, своего списка у него нет.
#
# Официальные пакеты ставятся через POST /api/v2/packages. Для локального пакета нужен свой
# индекс пакетов, но PISTON_REPO_URL принимает один индекс и скрыл бы официальные пакеты.
# Поэтому локальный пакет кладётся в каталог пакетов контейнера так же, как это делает
# api/src/package.js у Piston: распаковка, кэш окружения в .env, метка .ppman-installed.
# Piston читает каталог пакетов только при старте, поэтому контейнер перезапускается.
set -euo pipefail

usage() {
  cat <<'EOF'
piston-packages.sh — собирает и ставит в Piston пакеты окружений песочницы taskgen

  piston-packages.sh list                                   пакеты из реестра окружений
  piston-packages.sh install [-u URL] [-c CONTAINER] [-o DIR] [-i IMAGE] [-f]
                                                            поставить все пакеты из реестра
  piston-packages.sh build NAME VERSION [-o DIR] [-i IMAGE] собрать локальный пакет в архив

  -u URL         адрес Piston (по умолчанию $PISTON_URL, иначе http://localhost:2000)
  -c CONTAINER   контейнер Piston, куда кладутся локальные пакеты
                 (по умолчанию $PISTON_CONTAINER, иначе taskgen-piston-1)
  -o DIR         каталог для сборки и архивов локальных пакетов
                 (по умолчанию $TASKGEN_PISTON_BUILD, иначе taskgen/piston-packages
                 в $XDG_CACHE_HOME или в ~/.cache)
  -i IMAGE       образ, в котором идёт сборка; совпадает с образом Piston, чтобы собранное
                 работало с его glibc (по умолчанию ghcr.io/engineer-man/piston)
  -f             собрать локальный пакет заново, даже если архив уже есть

Локальный пакет — каталог sandbox/packages/NAME/VERSION в формате пакетов Piston.
Сборка python-ml скачивает около 1 ГБ и занимает несколько минут, сборка analyzers —
около 60 МБ.

Примеры:
  piston-packages.sh install -c tg-sandbox-piston-1
  piston-packages.sh build python-ml 3.12.0

Environment: PISTON_URL, PISTON_CONTAINER, TASKGEN_PISTON_BUILD, XDG_CACHE_HOME задают
значения по умолчанию
Сеть: скачивает официальные пакеты Piston и библиотеки для локальных пакетов
Exit 0 готово, 1 сборка или установка не удалась, 2 ошибка в вызове
EOF
}

fail() { # не удалась сборка или установка
  printf 'piston-packages.sh: %s\n' "$1" >&2
  exit 1
}

die() { # неверный вызов
  printf 'piston-packages.sh: %s\n' "$1" >&2
  exit 2
}

say() {
  printf '%s\n' "$1" >&2
}

HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT=$(cd -- "$HERE/.." && pwd)

url="${PISTON_URL:-http://localhost:2000}"
container="${PISTON_CONTAINER:-taskgen-piston-1}"
out="${TASKGEN_PISTON_BUILD:-${XDG_CACHE_HOME:-$HOME/.cache}/taskgen/piston-packages}"
image=ghcr.io/engineer-man/piston
force=0

packages() {
  uv run --quiet --project "$PROJECT" python -m taskgen.sandbox.registry
}

install_official() {
  local name="$1" version="$2" body status
  say "ставлю официальный пакет $name $version"
  body=$(printf '{"language":"%s","version":"%s"}' "$name" "$version")
  status=$(curl -sS -o "$TMP/answer" -w '%{http_code}' -X POST \
    -H 'Content-Type: application/json' -d "$body" "$url/api/v2/packages") ||
    fail "Piston недоступен по адресу $url"
  if [[ "$status" == 200 ]]; then
    return 0
  fi
  # Повторная установка — не ошибка: Piston отвечает 500 с этим текстом.
  if grep -q 'Already installed' "$TMP/answer"; then
    say "  уже установлен"
    return 0
  fi
  fail "Piston не поставил $name $version ($status): $(cat "$TMP/answer")"
}

archive_of() {
  printf '%s/%s-%s.pkg.tar.gz' "$out" "$1" "$2"
}

build_local() {
  local name="$1" version="$2" recipe work archive
  recipe="$HERE/packages/$name/$version"
  [[ -f "$recipe/build.sh" ]] || fail "нет локального пакета $recipe"
  archive=$(archive_of "$name" "$version")
  if [[ -f "$archive" ]] && ((! force)); then
    say "архив $archive уже есть, сборка пропущена (-f соберёт заново)"
    return 0
  fi
  work="$out/$name-$version"
  rm -rf "$work"
  mkdir -p "$work"
  cp -R "$recipe/." "$work/"
  # Общая часть рецептов на Python; build.sh подключает её из своего каталога.
  cp "$HERE/packages/python-base.sh" "$work/"
  say "собираю $name $version в $work"
  # Пользователь хоста, а не root контейнера: иначе каталог сборки не удалить без sudo.
  docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp \
    -v "$work:/build/$name/$version" "$image" \
    bash -c 'set -e
      cd "$1"
      bash ./build.sh
      python3 -c "import json
info = json.load(open(\"metadata.json\"))
info[\"build_platform\"] = \"docker-debian\"
json.dump(info, open(\"pkg-info.json\", \"w\"))"' _ "/build/$name/$version" ||
    fail "сборка $name $version не удалась"
  tar czf "$archive.part" -C "$work" .
  mv "$archive.part" "$archive"
  rm -rf "$work"
  say "архив: $archive"
}

load_local() {
  local name="$1" version="$2" archive
  archive=$(archive_of "$name" "$version")
  say "кладу $name $version в контейнер $container"
  docker exec -i "$container" bash -c 'set -euo pipefail
    dest="/piston/packages/$1/$2"
    rm -rf "$dest"
    mkdir -p "$dest"
    tar xzf - --no-same-owner -C "$dest"
    cd "$dest"
    env -i bash -c "cd \"$dest\"; source environment; env" |
      grep -Ev "^(PWD|OLDPWD|_|SHLVL)=" >.env
    date +%s000 >.ppman-installed' _ "$name" "$version" <"$archive" ||
    fail "не удалось положить пакет в контейнер $container"
}

restart_and_wait() {
  say "перезапускаю $container, Piston читает пакеты только при старте"
  docker restart "$container" >/dev/null || fail "не удалось перезапустить $container"
  wait_ready
}

# Контейнер, только что поднятый docker compose up, отвечает на запросы через несколько секунд.
wait_ready() {
  local tries=0
  until curl -sSf -o "$TMP/runtimes" "$url/api/v2/runtimes" 2>/dev/null; do
    tries=$((tries + 1))
    ((tries < 60)) || fail "Piston не ответил по адресу $url за 60 с"
    sleep 1
  done
}

cmd_list() {
  (($# == 0)) || die "list не принимает аргументов"
  packages
}

cmd_build() {
  local positional=()
  while (($#)); do
    case "$1" in
      -o)
        (($# >= 2)) || die "-o требует каталог"
        out="$2"
        shift 2
        ;;
      -i)
        (($# >= 2)) || die "-i требует образ"
        image="$2"
        shift 2
        ;;
      -f)
        force=1
        shift
        ;;
      -*) die "нет такого флага: $1" ;;
      *)
        positional+=("$1")
        shift
        ;;
    esac
  done
  ((${#positional[@]} == 2)) || die "build требует NAME и VERSION"
  build_local "${positional[0]}" "${positional[1]}"
}

cmd_install() {
  local list origin name version locals=0
  while (($#)); do
    case "$1" in
      -u)
        (($# >= 2)) || die "-u требует адрес"
        url="$2"
        shift 2
        ;;
      -c)
        (($# >= 2)) || die "-c требует имя контейнера"
        container="$2"
        shift 2
        ;;
      -o)
        (($# >= 2)) || die "-o требует каталог"
        out="$2"
        shift 2
        ;;
      -i)
        (($# >= 2)) || die "-i требует образ"
        image="$2"
        shift 2
        ;;
      -f)
        force=1
        shift
        ;;
      *) die "install не принимает аргумент $1" ;;
    esac
  done
  list=$(packages) || fail "не удалось прочитать реестр окружений"
  wait_ready
  while read -r origin name version; do
    case "$origin" in
      official) install_official "$name" "$version" ;;
      local)
        build_local "$name" "$version"
        load_local "$name" "$version"
        locals=1
        ;;
      *) fail "неизвестный источник пакета $origin" ;;
    esac
  done <<<"$list"
  if ((locals)); then
    restart_and_wait
  fi
  curl -sSf "$url/api/v2/runtimes" || fail "Piston не отдал список окружений"
  printf '\n'
}

TMP=$(mktemp -d "${TMPDIR:-/tmp}/piston-packages.XXXXXX")
trap 'rm -rf "$TMP"' EXIT

cmd="${1:-}"
(($# == 0)) || shift
case "$cmd" in
  list) cmd_list "$@" ;;
  install) cmd_install "$@" ;;
  build) cmd_build "$@" ;;
  -h | --help | help) usage ;;
  '')
    usage >&2
    exit 2
    ;;
  *)
    printf 'piston-packages.sh: нет такой подкоманды: %s\n\n' "$cmd" >&2
    usage >&2
    exit 2
    ;;
esac
