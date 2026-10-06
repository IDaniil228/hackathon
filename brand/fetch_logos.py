#!/usr/bin/env python3
"""Скачивает логотипы ФСП со страницы брендбука в brand/assets/logo/.

Брендбук не даёт явного разрешения распространять логотипы, поэтому они не лежат
в репозитории, а скачиваются с сайта ФСП и сверяются по SHA-256.
Запуск: python3 brand/fetch_logos.py
"""

import hashlib
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "assets" / "logo"
BASE_URL = "https://fsp-russia.ru/completed_pages/images/"

# Те же файлы лежат в архиве брендбука https://fsp-russia.ru/files/брендбук.rar
LOGOS = {
    "logo-full-on-dark.svg": ("logo_big.svg", "299f7b99fd539e0dbaaeee2ec1b1f83fd8397aef9a3273acef453ba040a72c2d"),
    "logo-full-on-light.svg": ("logo_01w.svg", "a9c302eefd2994004afaf288a72eb0750873a6ae8eefd1012fa54d2d3d8267e4"),
    "logo-short-on-dark.svg": ("logo_01.svg", "a1e81f88a89e93324d4dcc669b345428a9fa495bfe948ce633fdd4dcdfc507da"),
    "logo-short-on-light.svg": ("logo_02w.svg", "f0e59af3cd4d90592f76543a37ea1451e414f459436858c9a278671fa8d3a0fd"),
    "logo-sign-on-dark.svg": ("logo_02.svg", "2774aea7b35526445996731ff0ca90080dd9df6c541e701af13c4b671fc3d7dd"),
    "logo-sign-on-light.svg": ("logo_03w.svg", "b994c36570ac0bfe01f2d3e845de7dd27c1a5ee4eadbb1bae7cd4a00b48b794a"),
}


def fetch(url):
    with urllib.request.urlopen(url, timeout=30) as response:
        return response.read()


def main():
    TARGET.mkdir(parents=True, exist_ok=True)
    failed = []
    for name, (source, sha256) in LOGOS.items():
        data = fetch(BASE_URL + source)
        actual = hashlib.sha256(data).hexdigest()
        if actual != sha256:
            failed.append(f"{source}: ожидался SHA-256 {sha256}, получен {actual}")
            continue
        (TARGET / name).write_bytes(data)
        print(f"{name} <- {BASE_URL}{source}")
    for message in failed:
        print(f"ошибка: {message}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
