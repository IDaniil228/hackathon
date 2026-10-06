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


def load_tokens():
    return json.loads((HERE / "tokens.json").read_text(encoding="utf-8"))


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
