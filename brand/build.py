#!/usr/bin/env python3
"""Генератор brand/dist/tokens.css и brand/dist/tailwind.preset.js из brand/tokens.json.

Запуск: python3 brand/build.py         записать файлы
        python3 brand/build.py --check выйти с кодом 1, если файлы устарели
"""

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOKENS_PATH = HERE / "tokens.json"
DIST = HERE / "dist"
PREFIX = "fsp"
HEADER = "Сгенерировано brand/build.py из brand/tokens.json. Не редактируйте вручную"
REFERENCE = re.compile(r"\{([A-Za-z0-9_.-]+)\}")
HEX = re.compile(r"^#([0-9a-fA-F]{6})$")
THEMES = ("light", "dark")


class TokenError(ValueError):
    pass


def is_token(node):
    return isinstance(node, dict) and "$value" in node


def walk(node, path=()):
    """Обходит токены в порядке файла и отдаёт пары (путь, узел токена)."""
    for key, child in node.items():
        if key.startswith("$"):
            continue
        if is_token(child):
            yield path + (key,), child
        elif isinstance(child, dict):
            yield from walk(child, path + (key,))


def format_color(rgba):
    r, g, b, a = rgba
    if a >= 1:
        return f"#{r:02x}{g:02x}{b:02x}"
    return f"rgb({r} {g} {b} / {a:g})"


class Tokens:
    def __init__(self, data):
        self.data = data
        self.index = {".".join(path): node for path, node in walk(data)}
        self._validate_themes()

    def _validate_themes(self):
        themes = self.data.get("theme", {})
        keys = {name: [k for k in themes.get(name, {}) if not k.startswith("$")] for name in THEMES}
        missing = set(keys["light"]) ^ set(keys["dark"])
        if missing:
            raise TokenError(f"темы light и dark расходятся по ключам: {', '.join(sorted(missing))}")

    def node(self, path):
        if path not in self.index:
            raise TokenError(f"неизвестная ссылка на токен: {path}")
        return self.index[path]

    def color(self, path, _seen=()):
        """Вычисляет цвет токена как (r, g, b, alpha), следуя ссылкам и операциям."""
        if path in _seen:
            raise TokenError(f"циклическая ссылка: {' -> '.join(_seen + (path,))}")
        return self._color_value(self.node(path)["$value"], _seen + (path,))

    def _color_value(self, value, seen):
        if isinstance(value, dict):
            if "mix" in value:
                a, b, t = value["mix"]
                ca, cb = self._color_value(a, seen), self._color_value(b, seen)
                return tuple(round(x + (y - x) * t) for x, y in zip(ca[:3], cb[:3])) + (1.0,)
            if "alpha" in value:
                base, alpha = value["alpha"]
                return self._color_value(base, seen)[:3] + (float(alpha),)
            raise TokenError(f"неизвестная операция: {sorted(value)}")
        ref = REFERENCE.fullmatch(value)
        if ref:
            return self.color(ref.group(1), seen)
        match = HEX.match(value)
        if not match:
            raise TokenError(f"ожидался цвет #RRGGBB, получено: {value}")
        digits = match.group(1)
        return tuple(int(digits[i : i + 2], 16) for i in (0, 2, 4)) + (1.0,)

    def css_value(self, path):
        """Значение для CSS: ссылка становится var(), операция над цветом - вычисленным цветом."""
        value = self.node(path)["$value"]
        if isinstance(value, dict):
            return format_color(self.color(path))
        if isinstance(value, (int, float)):
            return str(value)
        if HEX.match(value):
            return format_color(self.color(path))
        return REFERENCE.sub(lambda m: f"var({css_name(self._check_ref(m.group(1)))})", value)

    def _check_ref(self, ref):
        # {theme.X} вне тем указывает на семантический цвет той темы, что активна в браузере
        parts = ref.split(".")
        if len(parts) == 2 and parts[0] == "theme":
            self.node(f"theme.light.{parts[1]}")
        else:
            self.node(ref)
        return ref

    def all_color_literals(self):
        """Все цвета, которые сборка может записать в dist."""
        return {
            format_color(self.color(path))
            for path in self.index
            if path.startswith(("palette.", "theme."))
        }


def css_name(path):
    """Имя CSS-переменной: theme.<тема>.X -> --fsp-color-X, остальные - путь через дефис."""
    parts = path.split(".")
    if parts[0] == "theme":
        if len(parts) == 2:
            return f"--{PREFIX}-color-{parts[1]}"
        return f"--{PREFIX}-color-{'-'.join(parts[2:])}"
    return f"--{PREFIX}-{'-'.join(parts)}"


def theme_keys(tokens):
    return [k for k in tokens.data["theme"]["light"] if not k.startswith("$")]


def render_css(tokens):
    lines = [f"/* {HEADER} */", "", ":root {", "  color-scheme: light;"]
    for path in tokens.index:
        if path.startswith("theme.dark."):
            continue
        if path.startswith("theme.light."):
            name = css_name("theme." + path.split(".", 2)[2])
        else:
            name = css_name(path)
        lines.append(f"  {name}: {tokens.css_value(path)};")
    lines.append("}")
    dark = [f"  {css_name('theme.' + key)}: {tokens.css_value('theme.dark.' + key)};" for key in theme_keys(tokens)]
    lines += ["", "@media (prefers-color-scheme: dark) {", '  :root:not([data-theme="light"]) {', "    color-scheme: dark;"]
    lines += ["  " + line for line in dark]
    lines += ["  }", "}", "", ':root[data-theme="dark"] {', "  color-scheme: dark;"]
    lines += dark
    lines += ["}", ""]
    return "\n".join(lines)


def js(value):
    return json.dumps(value, ensure_ascii=False)


def group_paths(tokens, prefix):
    return [p for p in tokens.index if p.startswith(prefix + ".")]


def render_tailwind(tokens):
    def var(path):
        return f"var({css_name(path)})"

    def alpha_color(path):
        # <alpha-value> подставляет Tailwind v3 при модификаторе прозрачности, например bg-fsp-primary/50
        return f"color-mix(in srgb, {var(path)} calc(<alpha-value> * 100%), transparent)"

    colors = {key: alpha_color(f"theme.{key}") for key in theme_keys(tokens)}
    colors["palette"] = {p.split(".")[1]: alpha_color(p) for p in group_paths(tokens, "palette")}
    font_family = {p.split(".")[2]: var(p) for p in group_paths(tokens, "font.family")}
    font_weight = {p.split(".")[2]: var(p) for p in group_paths(tokens, "font.weight")}
    sizes = [key for key in tokens.data["text"] if not key.startswith("$")]
    font_size = {s: [var(f"text.{s}.size"), {"lineHeight": var(f"text.{s}.leading")}] for s in sizes}
    spacing = {p.split(".")[1]: var(p) for p in group_paths(tokens, "space")}
    radius = {p.split(".")[1]: var(p) for p in group_paths(tokens, "radius")}
    shadow = {p.split(".")[1]: var(p) for p in group_paths(tokens, "shadow")}
    control = {f"control-{p.split('.')[1]}": var(p) for p in group_paths(tokens, "control")}
    # В screens нужны литералы: внутри @media переменная CSS не вычисляется
    screens = {p.split(".")[1]: tokens.css_value(p) for p in group_paths(tokens, "breakpoint")}
    duration = {p.split(".")[1]: var(p) for p in group_paths(tokens, "duration")}
    easing = {f"fsp-{p.split('.')[1]}": var(p) for p in group_paths(tokens, "easing")}
    preset = {
        "theme": {
            "screens": screens,
            "extend": {
                "colors": {"fsp": colors},
                "fontFamily": font_family,
                "fontWeight": font_weight,
                "fontSize": font_size,
                "spacing": spacing,
                "height": control,
                "minHeight": control,
                "borderRadius": radius,
                "boxShadow": shadow,
                "transitionDuration": duration,
                "transitionTimingFunction": easing,
            },
        },
    }
    body = json.dumps(preset, ensure_ascii=False, indent=2)
    return (
        f"// {HEADER}\n"
        "// Пресет Tailwind CSS v3: подключите brand/dist/tokens.css и укажите\n"
        "// presets: [require('./brand/dist/tailwind.preset.js')] в tailwind.config.js\n"
        f"module.exports = {body};\n"
    )


def render(data):
    tokens = Tokens(data)
    return {
        "tokens.css": render_css(tokens),
        "tailwind.preset.js": render_tailwind(tokens),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Собирает dist из brand/tokens.json")
    parser.add_argument("--check", action="store_true", help="не писать файлы, а проверить, что они актуальны")
    args = parser.parse_args(argv)
    outputs = render(json.loads(TOKENS_PATH.read_text(encoding="utf-8")))
    stale = []
    for name, content in outputs.items():
        target = DIST / name
        current = target.read_text(encoding="utf-8") if target.exists() else None
        if current == content:
            continue
        stale.append(name)
        if not args.check:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
    if args.check and stale:
        print(f"устарели: {', '.join(stale)}; запустите python3 brand/build.py", file=sys.stderr)
        return 1
    if args.check:
        print("dist актуален")
    else:
        print(f"обновлены: {', '.join(stale) or 'нет изменений'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
