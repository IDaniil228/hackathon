"""Тесты сборки токенов и проверки контраста.

Запуск без внешних зависимостей: python3 -m unittest discover -s brand -p 'test_*.py'
"""

import copy
import json
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import build  # noqa: E402
import check_contrast  # noqa: E402

DIST = HERE / "dist"
COLOR_LITERAL = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\([^)]*\)|\bhsla?\([^)]*\)")
BRANDBOOK_PAGE = re.compile(r"^Брендбук ФСП\.pdf, стр\. \d+")
FONT_FACE = re.compile(r"@font-face\s*\{([^}]*)\}")
# Брендбук называет эти серые цветами "для фона и шрифта", поэтому нейтральные роли тем берут их
# прямой ссылкой, а не смесью
OFFICIAL_NEUTRALS = {
    "light": ("bg", "surface-hover", "border", "text", "neutral-soft"),
    "dark": ("bg", "surface", "surface-hover", "border", "border-strong", "text", "text-muted", "neutral-soft"),
}


def load_tokens():
    return json.loads((HERE / "tokens.json").read_text(encoding="utf-8"))


def palette_ref(value):
    """Имя цвета палитры, если значение - прямая ссылка {palette.X}, иначе None."""
    match = isinstance(value, str) and re.fullmatch(r"\{palette\.([a-z0-9-]+)\}", value)
    return match.group(1) if match else None


class PaletteTest(unittest.TestCase):
    def test_every_palette_color_cites_a_brandbook_page_or_is_derived(self):
        for name, node in load_tokens()["palette"].items():
            if name.startswith("$"):
                continue
            with self.subTest(color=name):
                if "$derived" in node:
                    self.assertNotIn("$source", node)
                else:
                    self.assertRegex(node.get("$source", ""), BRANDBOOK_PAGE)

    def test_neutral_roles_use_official_grays(self):
        tokens = load_tokens()
        palette = tokens["palette"]
        for theme, keys in OFFICIAL_NEUTRALS.items():
            for key in keys:
                with self.subTest(theme=theme, key=key):
                    name = palette_ref(tokens["theme"][theme][key]["$value"])
                    self.assertIsNotNone(name, "ожидалась прямая ссылка на палитру, а не смесь")
                    self.assertNotIn("$derived", palette[name], f"palette.{name} не из брендбука")


class FontTest(unittest.TestCase):
    def font_faces(self):
        css = (DIST / "base.css").read_text(encoding="utf-8")
        for body in FONT_FACE.findall(css):
            family = re.search(r'font-family:\s*"([^"]+)"', body).group(1)
            yield family, re.findall(r'url\("([^"]+)"\)', body)

    def test_brandbook_fonts_cite_a_page(self):
        families = load_tokens()["font"]["family"]
        # Основной и акцентный шрифты со стр. 21 и 22 брендбука
        for key in ("brand", "accent"):
            with self.subTest(family=key):
                self.assertRegex(families[key].get("$source", ""), BRANDBOOK_PAGE)

    def test_every_font_token_family_has_a_font_face(self):
        faces = {family for family, _ in self.font_faces()}
        for key, node in load_tokens()["font"]["family"].items():
            first = re.match(r'"([^"]+)"', node["$value"])
            if first:
                with self.subTest(family=key):
                    self.assertIn(first.group(1), faces)

    def test_font_files_exist_beside_their_license(self):
        for family, urls in self.font_faces():
            with self.subTest(family=family):
                self.assertTrue(urls)
                license_file = DIST / "fonts" / f"{family.replace(' ', '')}-OFL.txt"
                self.assertTrue(license_file.is_file(), f"нет текста лицензии {license_file.name}")
                for url in urls:
                    self.assertTrue((DIST / url).is_file(), f"нет файла {url}")


class BuildTest(unittest.TestCase):
    def test_render_is_deterministic(self):
        self.assertEqual(build.render(load_tokens()), build.render(load_tokens()))

    def test_committed_dist_matches_render(self):
        for name, content in build.render(load_tokens()).items():
            with self.subTest(file=name):
                committed = (DIST / name).read_text(encoding="utf-8")
                self.assertEqual(committed, content, f"{name} устарел: запустите python3 brand/build.py")

    def test_color_literals_only_in_palette(self):
        tokens = load_tokens()
        outside = copy.deepcopy(tokens)
        del outside["palette"]
        found = COLOR_LITERAL.findall(json.dumps(outside, ensure_ascii=False))
        self.assertEqual(found, [], "литерал цвета вне palette")

    def test_every_dist_color_is_resolved_from_tokens(self):
        resolved = build.Tokens(load_tokens()).all_color_literals()
        for name in build.render(load_tokens()):
            text = (DIST / name).read_text(encoding="utf-8")
            for literal in COLOR_LITERAL.findall(text):
                with self.subTest(file=name, color=literal):
                    self.assertIn(literal.lower(), resolved)

    def test_handwritten_files_have_no_color_literals(self):
        for path in (DIST / "base.css", HERE / "preview.html"):
            with self.subTest(file=path.name):
                self.assertEqual(COLOR_LITERAL.findall(path.read_text(encoding="utf-8")), [])

    def test_media_query_widths_are_breakpoint_tokens(self):
        # CSS-переменная не работает внутри @media, поэтому ширина повторена литералом
        breakpoints = {
            build.Tokens(load_tokens()).css_value(f"breakpoint.{key}")
            for key in load_tokens()["breakpoint"]
            if not key.startswith("$")
        }
        css = (DIST / "base.css").read_text(encoding="utf-8")
        widths = re.findall(r"@media[^{]*?(\d+(?:\.\d+)?px)", css)
        self.assertTrue(widths, "в base.css нет медиазапросов по ширине: тест ничего не проверяет")
        for width in widths:
            with self.subTest(width=width):
                self.assertIn(width, breakpoints)

    def test_themes_must_have_same_keys(self):
        tokens = load_tokens()
        del tokens["theme"]["dark"]["link"]
        with self.assertRaisesRegex(build.TokenError, "link"):
            build.render(tokens)

    def test_unknown_reference_is_an_error(self):
        tokens = load_tokens()
        tokens["theme"]["light"]["link"]["$value"] = "{palette.purple}"
        with self.assertRaisesRegex(build.TokenError, "palette.purple"):
            build.render(tokens)

    def test_mix_moves_from_first_color_towards_second(self):
        data = load_tokens()
        data["theme"]["light"]["surface-muted"]["$value"] = {"mix": ["{palette.white}", "{palette.dark}", 0.75]}
        tokens = build.Tokens(data)
        white, dark = tokens.color("palette.white"), tokens.color("palette.dark")
        mixed = tokens.color("theme.light.surface-muted")
        for channel in range(3):
            self.assertTrue(min(white[channel], dark[channel]) <= mixed[channel] <= max(white[channel], dark[channel]))
            self.assertLess(abs(mixed[channel] - dark[channel]), abs(mixed[channel] - white[channel]))


class ContrastTest(unittest.TestCase):
    def test_known_ratio(self):
        black, white = (0, 0, 0, 1.0), (255, 255, 255, 1.0)
        self.assertAlmostEqual(check_contrast.contrast_ratio(black, white), 21.0, places=2)

    def test_all_declared_pairs_pass(self):
        results = check_contrast.evaluate(build.Tokens(load_tokens()), check_contrast.load_pairs())
        failed = [r for r in results if not r.ok]
        self.assertEqual(failed, [])

    def test_small_white_text_on_brand_red_fails(self):
        tokens = load_tokens()
        tokens["theme"]["light"]["danger"]["$value"] = "{palette.red}"
        pairs = [{"fg": "text-on-primary", "bg": "danger", "kind": "text", "where": "test"}]
        results = check_contrast.evaluate(build.Tokens(tokens), pairs)
        self.assertFalse(results[0].ok)

    def test_pairs_cover_base_css(self):
        pairs = check_contrast.load_pairs()
        declared = {(p["fg"], p["bg"]) for p in pairs}
        foregrounds = {p["fg"] for p in pairs}
        css = (DIST / "base.css").read_text(encoding="utf-8")
        for selector, body in check_contrast.rule_blocks(css):
            fg = check_contrast.var_of(body, "color")
            bg = check_contrast.var_of(body, "background-color") or check_contrast.var_of(body, "background")
            with self.subTest(selector=selector):
                if fg:
                    self.assertIn(fg, foregrounds, f"цвет текста {fg} не проверяется")
                if fg and bg:
                    self.assertIn((fg, bg), declared, f"пара {fg} на {bg} не перечислена")


if __name__ == "__main__":
    unittest.main()
