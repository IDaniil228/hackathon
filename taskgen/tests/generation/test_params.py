"""Параметры экземпляра выбирает код по seed."""

import random

from taskgen.contracts import ParamSpec
from taskgen.generation.params import draw_params, fill_placeholders

SPECS = {
    "n": ParamSpec(type="int", min=10, max=20),
    "ratio": ParamSpec(type="float", min=0.5, max=1.5),
    "mode": ParamSpec(type="choice", choices=["sum", "max", "min"]),
}


def test_same_seed_gives_same_params():
    assert draw_params(SPECS, random.Random(42)) == draw_params(SPECS, random.Random(42))


def test_values_respect_spec():
    seen_n: set[int] = set()
    seen_mode: set[str] = set()
    for seed in range(300):
        params = draw_params(SPECS, random.Random(seed))
        assert type(params["n"]) is int
        assert 10 <= params["n"] <= 20
        assert 0.5 <= params["ratio"] <= 1.5
        assert params["mode"] in {"sum", "max", "min"}
        seen_n.add(params["n"])
        seen_mode.add(params["mode"])
    # Обе границы целого диапазона достижимы, и каждый вариант выбора встречается.
    assert {10, 20} <= seen_n
    assert seen_mode == {"sum", "max", "min"}


def test_draw_does_not_depend_on_declaration_order():
    reordered = dict(reversed(list(SPECS.items())))
    assert draw_params(reordered, random.Random(7)) == draw_params(SPECS, random.Random(7))


def test_int_bounds_from_yaml_floats_stay_integers():
    params = draw_params({"k": ParamSpec(type="int", min=1.0, max=1.0)}, random.Random(0))
    assert params == {"k": 1}
    assert type(params["k"]) is int


def test_fill_placeholders_substitutes_and_unescapes():
    text = "Массив длины {n}, режим {mode}; литерал {{n}} не трогаем, {unknown} тоже"
    filled = fill_placeholders(text, {"n": 12, "mode": "sum"})
    assert filled == "Массив длины 12, режим sum; литерал {n} не трогаем, {unknown} тоже"
