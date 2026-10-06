"""Бенчмарк проверки ситуации эссе: режимы llm, embeddings и both на размеченном наборе.

Разметка известна по построению. Хорошие ситуации и подсказки написаны вручную в
tests/generation/bench/essays/*.yaml, а варианты собираются из них кодом:

- good: хорошая ситуация, все факты есть, запрещённого нет
- deletion: удалено предложение, которое несёт факт; этот факт отсутствует
- paraphrase: в конец добавлена подсказка к запрещённой фразе без общих с ней слов
- negation: предложение с фактом заменено его отрицанием; факт отсутствует
- fixture: настоящая генерация с разметкой из tests/generation/bench/fixtures.yaml

Команды:

    uv run python scripts/essay_check_bench.py build          # пересобрать cases.json
    uv run python scripts/essay_check_bench.py build --check  # cases.json совпадает с источниками
    uv run python scripts/essay_check_bench.py run --out results.json

Порог эмбеддингов подбирается на половине эссе (tune) и меряется на другой половине (test):
варианты одного эссе не попадают в обе половины. LLM медленная, поэтому режимы llm и both
идут на стратифицированной по виду варианта подвыборке с фиксированным seed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from taskgen.bank.parser import find_phrases, normalize_phrase, parse_template
from taskgen.config import Settings
from taskgen.contracts import ChatMessage, TemplateSpec
from taskgen.generation.essay import (
    EmbeddingCache,
    EssayCheckOptions,
    EssayScenario,
    check_essay_scenario,
    embedding_scores,
    filled_facts,
    split_sentences,
)
from taskgen.llm import OpenAICompatibleClient

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "seed" / "templates"
BENCH = ROOT / "tests" / "generation" / "bench"
CASES = BENCH / "cases.json"
SPLIT_SEED = 20261006
TUNE_ESSAYS = 5
KINDS = ("good", "deletion", "paraphrase", "negation", "fixture")


class BenchDataError(Exception):
    """Исходные данные бенчмарка нарушают правило построения разметки."""


# --- Сборка вариантов -------------------------------------------------------------------------


@cache
def load_spec(relative: str) -> TemplateSpec:
    path = TEMPLATES / relative
    return parse_template(path.read_text(encoding="utf-8"), source=relative)


def is_code(unit: str) -> bool:
    return unit.startswith("```")


def join_units(units: list[str]) -> str:
    """Ситуация из единиц: проза через пробел, блок кода отделён пустыми строками."""
    text = units[0] if units else ""
    for previous, unit in zip(units, units[1:], strict=False):
        text += ("\n\n" if is_code(unit) or is_code(previous) else " ") + unit
    return text


def _stems(text: str) -> set[str]:
    """Начала слов длиной до 5 букв: так "утечк" и "утечка" считаются общим словом."""
    return {word[:5] for word in normalize_phrase(text).split() if len(word) >= 3}


def build_cases(essays_dir: Path = BENCH / "essays", fixtures: Path = BENCH / "fixtures.yaml"):
    cases: list[dict[str, Any]] = []
    for source in sorted(essays_dir.glob("*.yaml")):
        cases.extend(_essay_cases(source))
    for entry in yaml.safe_load(fixtures.read_text(encoding="utf-8")):
        cases.append(_fixture_case(entry, fixtures.parent))
    return cases


def _essay_cases(source: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    spec = load_spec(data["template"])
    essay = spec.name
    fact_ids = [fact.id for fact in spec.scenario_facts]
    skip = data.get("skip_deletion") or {}
    _require(set(skip) <= set(fact_ids), f"{essay}: skip_deletion называет неизвестные факты")
    cases: list[dict[str, Any]] = []
    goods: dict[str, dict[str, Any]] = {}

    def case(kind: str, sid: str, suffix: str, units: list[str], **labels: Any) -> None:
        good = goods[sid]
        situation = join_units(units)
        cases.append(
            {
                "id": f"{essay}/{sid}/{suffix}",
                "essay": essay,
                "template": data["template"],
                "kind": kind,
                "params": good["params"],
                "title": good["title"],
                "situation": situation,
                "missing_facts": labels.get("missing_facts", []),
                "forbidden_found": labels.get("forbidden_found", []),
                "negated_fact": labels.get("negated_fact"),
            }
        )

    for good in data["situations"]:
        sid = good["id"]
        goods[sid] = good
        texts = [unit["text"] for unit in good["units"]]
        carriers = Counter(fid for unit in good["units"] for fid in unit["facts"])
        _require(
            sorted(carriers) == sorted(fact_ids) and set(carriers.values()) == {1},
            f"{essay}/{sid}: каждый факт должен нести ровно одно предложение",
        )
        situation = join_units(texts)
        _require(
            split_sentences(situation) == texts,
            f"{essay}/{sid}: split_sentences режет ситуацию не по разметке",
        )
        literal = find_phrases(f"{good['title']}\n{situation}", spec.forbidden)
        _require(not literal, f"{essay}/{sid}: в хорошей ситуации есть {literal}")
        case("good", sid, "good", texts)
        for index, unit in enumerate(good["units"]):
            if not unit["facts"] or set(unit["facts"]) & set(skip):
                continue
            rest = texts[:index] + texts[index + 1 :]
            case("deletion", sid, f"delete-{index}", rest, missing_facts=list(unit["facts"]))

    forbidden = set(spec.forbidden)
    for number, hint in enumerate(data["paraphrases"]):
        phrases = hint["phrases"]
        _require(set(phrases) <= forbidden, f"{essay}: подсказка к фразам не из шаблона")
        _require(not find_phrases(hint["text"], spec.forbidden), f"{essay}: подсказка дословна")
        shared = _stems(hint["text"]) & set().union(*(_stems(p) for p in phrases))
        _require(not shared, f"{essay}: у подсказки общие слова с фразой: {sorted(shared)}")
        for sid, good in goods.items():
            texts = [unit["text"] for unit in good["units"]] + [hint["text"]]
            case("paraphrase", sid, f"paraphrase-{number}", texts, forbidden_found=phrases)

    for negation in data["negations"]:
        sid, fact = negation["situation"], negation["fact"]
        units = goods[sid]["units"]
        [index] = [i for i, unit in enumerate(units) if fact in unit["facts"]]
        _require(units[index]["facts"] == [fact], f"{essay}: отрицание задевает другие факты")
        _require(not find_phrases(negation["text"], spec.forbidden), f"{essay}: отрицание с фразой")
        texts = [unit["text"] for unit in units]
        texts[index] = negation["text"]
        case(
            "negation",
            sid,
            f"negate-{fact}",
            texts,
            missing_facts=[fact],
            negated_fact=fact,
        )
    return cases


def _fixture_case(entry: dict[str, Any], base: Path) -> dict[str, Any]:
    sample = json.loads((base / entry["sample"]).read_text(encoding="utf-8"))
    spec = load_spec(entry["template"])
    known = {fact.id for fact in spec.scenario_facts}
    _require(set(entry["missing_facts"]) <= known, f"{entry['id']}: неизвестные факты")
    _require(set(entry["forbidden_found"]) <= set(spec.forbidden), f"{entry['id']}: не те фразы")
    return {
        "id": f"{spec.name}/{entry['id']}",
        "essay": spec.name,
        "template": entry["template"],
        "kind": "fixture",
        "params": sample["params"],
        "title": sample["public"]["title"],
        "situation": sample["statement"],
        "missing_facts": entry["missing_facts"],
        "forbidden_found": entry["forbidden_found"],
        "negated_fact": None,
    }


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BenchDataError(message)


def render_cases(cases: list[dict[str, Any]]) -> str:
    return json.dumps(cases, ensure_ascii=False, indent=1) + "\n"


# --- Разбиение и подвыборка -------------------------------------------------------------------


def split_essays(cases: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    essays = sorted({case["essay"] for case in cases})
    tune = sorted(random.Random(SPLIT_SEED).sample(essays, TUNE_ESSAYS))
    return tune, [essay for essay in essays if essay not in tune]


def llm_sample(cases: list[dict[str, Any]], size: int, seed: int) -> list[str]:
    """Подвыборка, где доля каждого вида варианта как в наборе, а fixture есть всегда."""
    if size >= len(cases):
        return [case["id"] for case in cases]
    by_kind: dict[str, list[str]] = defaultdict(list)
    for case in cases:
        by_kind[case["kind"]].append(case["id"])
    rng = random.Random(seed)
    chosen: list[str] = []
    for kind in KINDS:
        ids = by_kind.get(kind, [])
        share = len(ids) if kind == "fixture" else round(size * len(ids) / len(cases))
        chosen.extend(rng.sample(ids, min(len(ids), max(1, share))))
    return chosen


# --- Прогон -----------------------------------------------------------------------------------


class CountingLLM:
    """LLMClient, который считает вызовы: complete_structured может повторить запрос."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls = 0

    async def complete(self, messages: list[ChatMessage], **kwargs: Any) -> str:
        self.calls += 1
        return await self.inner.complete(messages, **kwargs)


@dataclass
class Row:
    case_id: str
    mode: str
    seconds: float
    llm_calls: int
    missing_facts: list[str]
    forbidden_found: list[str]
    method: str
    notes: list[str]


async def run_check(
    case: dict[str, Any],
    mode: str,
    llm: CountingLLM,
    embedder: Any,
    cache: EmbeddingCache,
    options: EssayCheckOptions,
    model: str,
    effort: str | None,
) -> Row:
    spec = load_spec(case["template"])
    scenario = EssayScenario(title=case["title"], situation=case["situation"])
    before = llm.calls
    started = time.perf_counter()
    result = await check_essay_scenario(
        llm,
        spec,
        scenario,
        case["params"],
        model=model,
        reasoning_effort=effort,
        options=options,
        embedder=embedder,
        cache=cache,
    )
    return Row(
        case_id=case["id"],
        mode=mode,
        seconds=time.perf_counter() - started,
        llm_calls=llm.calls - before,
        missing_facts=result.missing_facts,
        forbidden_found=result.forbidden_found,
        method=result.method,
        notes=result.notes,
    )


async def all_scores(cases, embedder) -> dict[str, dict[str, Any]]:
    """Сходства всех фактов и всех фраз шаблона: по ним подбираются пороги."""
    cache = EmbeddingCache()
    scores: dict[str, dict[str, Any]] = {}
    for case in cases:
        spec = load_spec(case["template"])
        facts = filled_facts(spec, case["params"])
        result = await embedding_scores(
            embedder, cache, facts, list(spec.forbidden), case["title"], case["situation"]
        )
        scores[case["id"]] = {
            "facts": {fid: match.score for fid, match in result.facts.items()},
            "phrases": {phrase: match.score for phrase, match in result.phrases.items()},
            "literal": find_phrases(f"{case['title']}\n{case['situation']}", spec.forbidden),
        }
    return scores


def predict(score: dict[str, Any], fact_threshold: float, forbidden_threshold: float):
    missing = [fid for fid, value in score["facts"].items() if value < fact_threshold]
    found = [
        phrase
        for phrase, value in score["phrases"].items()
        if phrase in score["literal"] or value >= forbidden_threshold
    ]
    return missing, found


def tune_threshold(values: list[float], f1_at) -> tuple[float, float]:
    """Порог с наибольшим F1; из равных берётся середина самого длинного плато."""
    scored = [(threshold, f1_at(threshold)) for threshold in values]
    best = max(f1 for _, f1 in scored)
    runs: list[list[float]] = []
    for threshold, f1 in scored:
        if math.isclose(f1, best):
            if runs and runs[-1][-1] == values[values.index(threshold) - 1]:
                runs[-1].append(threshold)
            else:
                runs.append([threshold])
    longest = max(runs, key=len)
    return round(longest[len(longest) // 2], 3), best


def grid() -> list[float]:
    return [round(0.30 + 0.005 * step, 3) for step in range(int((0.95 - 0.30) / 0.005) + 1)]


# --- Метрики ----------------------------------------------------------------------------------


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def evaluate(rows: list[dict[str, Any]], cases: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Положительный класс — нарушение: факт отсутствует или запрещённая фраза найдена."""
    fact = Counter()
    forbidden = Counter()
    negation = Counter()
    paraphrase = Counter()
    goods = Counter()
    verdicts = Counter()
    for row in rows:
        case = cases[row["case_id"]]
        spec = load_spec(case["template"])
        expected_missing, got_missing = set(case["missing_facts"]), set(row["missing_facts"])
        for fid in (f.id for f in spec.scenario_facts):
            fact[_cell(fid in expected_missing, fid in got_missing)] += 1
        expected_found, got_found = set(case["forbidden_found"]), set(row["forbidden_found"])
        for phrase in spec.forbidden:
            forbidden[_cell(phrase in expected_found, phrase in got_found)] += 1
        if case["kind"] == "negation":
            negation["all"] += 1
            negation["error"] += case["negated_fact"] not in got_missing
        if case["kind"] == "paraphrase":
            paraphrase["all"] += len(expected_found)
            paraphrase["hit"] += len(expected_found & got_found)
        expected_ok = not expected_missing and not expected_found
        got_ok = not got_missing and not got_found
        verdicts["all"] += 1
        verdicts["right"] += expected_ok == got_ok
        if case["kind"] == "good":
            goods["all"] += 1
            goods["rejected"] += not got_ok
    times = sorted(row["seconds"] for row in rows)
    return {
        "n": len(rows),
        "fact": prf(fact["tp"], fact["fp"], fact["fn"]),
        "forbidden": prf(forbidden["tp"], forbidden["fp"], forbidden["fn"]),
        "negation_error": _share(negation["error"], negation["all"]),
        "paraphrase_recall": _share(paraphrase["hit"], paraphrase["all"]),
        "good_rejected": _share(goods["rejected"], goods["all"]),
        "verdict_accuracy": _share(verdicts["right"], verdicts["all"]),
        "mean_seconds": sum(times) / len(times) if times else 0.0,
        "p95_seconds": times[math.ceil(0.95 * len(times)) - 1] if times else 0.0,
        "llm_calls": sum(row["llm_calls"] for row in rows),
    }


def _cell(expected: bool, got: bool) -> str:
    return {(True, True): "tp", (False, True): "fp", (True, False): "fn"}.get((expected, got), "tn")


def _share(part: int, whole: int) -> tuple[int, int]:
    return part, whole


def table(title: str, results: list[tuple[str, dict[str, Any]]]) -> str:
    def pct(pair: tuple[int, int]) -> str:
        part, whole = pair
        return f"{part}/{whole}" + (f" ({100 * part / whole:.0f} %)" if whole else "")

    head = (
        "| Режим | N | Факты P/R/F1 | Запрещённое P/R/F1 | Ошибки на отрицании | "
        "Перефразировка найдена | Хорошие отклонены | Вердикт верен | Среднее, с | p95, с | "
        "Вызовов LLM |"
    )
    lines = [f"\n### {title}\n", head, "|" + "---|" * 11]
    for name, m in results:
        fact = "/".join(f"{value:.2f}" for value in m["fact"])
        forb = "/".join(f"{value:.2f}" for value in m["forbidden"])
        lines.append(
            f"| {name} | {m['n']} | {fact} | {forb} | {pct(m['negation_error'])} | "
            f"{pct(m['paraphrase_recall'])} | {pct(m['good_rejected'])} | "
            f"{pct(m['verdict_accuracy'])} | {m['mean_seconds']:.2f} | "
            f"{m['p95_seconds']:.2f} | {m['llm_calls']} |"
        )
    return "\n".join(lines)


# --- Команда run ------------------------------------------------------------------------------


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def load_partial(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


async def run(args: argparse.Namespace) -> int:
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    if render_cases(build_cases()) != CASES.read_text(encoding="utf-8"):
        log("cases.json не совпадает с источниками: выполните build")
        return 1
    by_id = {case["id"]: case for case in cases}
    tune, test = split_essays(cases)
    half = {case["id"]: "tune" if case["essay"] in tune else "test" for case in cases}
    log(
        f"вариантов {len(cases)}: "
        + ", ".join(f"{k} {v}" for k, v in Counter(case["kind"] for case in cases).items())
    )
    log(f"tune: {tune}\ntest: {test}")

    settings = Settings().llm.model_copy(update={"timeout_seconds": args.timeout})
    client = OpenAICompatibleClient(settings)
    llm = CountingLLM(client)
    log(f"эмбеддер {client.model_name} по адресу {settings.base_url}")

    started = time.perf_counter()
    scores = await all_scores(cases, client)
    log(f"сходства посчитаны за {time.perf_counter() - started:.1f} с")

    tune_ids = [cid for cid in by_id if half[cid] == "tune"]

    def fact_f1(threshold: float) -> float:
        rows = []
        for cid in tune_ids:
            missing, _ = predict(scores[cid], threshold, 2.0)
            rows.append(
                {
                    "case_id": cid,
                    "missing_facts": missing,
                    "forbidden_found": [],
                    "seconds": 0.0,
                    "llm_calls": 0,
                }
            )
        return evaluate(rows, by_id)["fact"][2]

    def forbidden_f1(threshold: float) -> float:
        rows = []
        for cid in tune_ids:
            _, found = predict(scores[cid], -2.0, threshold)
            rows.append(
                {
                    "case_id": cid,
                    "missing_facts": [],
                    "forbidden_found": found,
                    "seconds": 0.0,
                    "llm_calls": 0,
                }
            )
        return evaluate(rows, by_id)["forbidden"][2]

    fact_threshold, fact_best = tune_threshold(grid(), fact_f1)
    forbidden_threshold, forbidden_best = tune_threshold(grid(), forbidden_f1)
    log(
        f"порог фактов {fact_threshold} (F1 на tune {fact_best:.3f}), "
        f"порог запрещённого {forbidden_threshold} (F1 на tune {forbidden_best:.3f})"
    )

    def opts(method: str) -> EssayCheckOptions:
        return EssayCheckOptions(
            method=method,
            fact_threshold=fact_threshold,
            forbidden_threshold=forbidden_threshold,
            grey_zone=args.grey_zone,
        )

    # Промежуточные строки переживают обрыв долгого прогона LLM. Ключ включает всё, от чего
    # зависит результат, поэтому строки с другими порогами или моделью не подхватываются.
    partial = Path(f"{args.out}.partial.jsonl")
    config = (
        f"{fact_threshold}|{forbidden_threshold}|{args.grey_zone}|{client.model_name}|"
        f"{args.checker_model}|{args.reasoning_effort}"
    )
    done = {
        (row["case_id"], row["mode"]): row
        for row in load_partial(partial)
        if row.get("config") == config
    }

    async def record(case: dict[str, Any], mode: str, cache: EmbeddingCache) -> dict[str, Any]:
        key = (case["id"], mode)
        if key in done:
            return done[key]
        row = asdict(
            await run_check(
                case,
                mode,
                llm,
                client,
                cache,
                opts(mode),
                args.checker_model,
                args.reasoning_effort,
            )
        )
        row["config"] = config
        with partial.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        done[key] = row
        return row

    # Эмбеддинги — на всём наборе; кэш общий, как у сервиса между экземплярами.
    emb_cache = EmbeddingCache()
    emb_rows = [await record(case, "embeddings", emb_cache) for case in cases]
    for row in emb_rows:
        expected = predict(scores[row["case_id"]], fact_threshold, forbidden_threshold)
        if (sorted(expected[0]), sorted(expected[1])) != (
            sorted(row["missing_facts"]),
            sorted(row["forbidden_found"]),
        ):
            log(f"расхождение подбора и проверки на {row['case_id']}")
            return 1

    sample = llm_sample(cases, args.llm_sample, args.seed)
    log(
        f"подвыборка для LLM: {len(sample)} вариантов: "
        + ", ".join(f"{k} {v}" for k, v in Counter(by_id[cid]["kind"] for cid in sample).items())
    )
    llm_rows: dict[str, list[dict[str, Any]]] = {"llm": [], "both": []}
    both_cache = EmbeddingCache()
    for number, cid in enumerate(sample, start=1):
        for mode in args.modes:
            row = await record(by_id[cid], mode, both_cache)
            llm_rows[mode].append(row)
            log(
                f"[{number}/{len(sample)}] {mode} {cid}: {row['seconds']:.1f} с, "
                f"вызовов {row['llm_calls']}"
            )
    await client.aclose()

    def subset(rows, ids):
        return [row for row in rows if row["case_id"] in ids]

    ids_tune = {cid for cid in by_id if half[cid] == "tune"}
    ids_test = set(by_id) - ids_tune
    ids_sample = set(sample)
    output = [
        f"Пороги: факты {fact_threshold}, запрещённое {forbidden_threshold}, "
        f"серая зона ±{args.grey_zone}; эмбеддер {client.model_name}, "
        f"проверяльщик {args.checker_model}, reasoning_effort {args.reasoning_effort}",
        f"tune ({len(ids_tune)} вариантов): {', '.join(tune)}",
        f"test ({len(ids_test)} вариантов): {', '.join(test)}",
        table(
            "Эмбеддинги на всём наборе",
            [
                ("embeddings, tune", evaluate(subset(emb_rows, ids_tune), by_id)),
                ("embeddings, test", evaluate(subset(emb_rows, ids_test), by_id)),
                ("embeddings, все", evaluate(emb_rows, by_id)),
            ],
        ),
    ]
    for name, ids in (("вся подвыборка", ids_sample), ("подвыборка ∩ test", ids_sample & ids_test)):
        results = [("embeddings", evaluate(subset(emb_rows, ids), by_id))]
        for mode in args.modes:
            results.append((mode, evaluate(subset(llm_rows[mode], ids), by_id)))
        output.append(table(f"Сравнение режимов: {name}", results))
    report = "\n".join(output)
    print(report)
    Path(args.out).write_text(
        json.dumps(
            {
                "fact_threshold": fact_threshold,
                "forbidden_threshold": forbidden_threshold,
                "grey_zone": args.grey_zone,
                "tune": tune,
                "test": test,
                "sample": sample,
                "rows": [*emb_rows, *llm_rows["llm"], *llm_rows["both"]],
                "report": report,
            },
            ensure_ascii=False,
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="собрать cases.json из размеченных источников")
    build.add_argument("--check", action="store_true", help="только сверить cases.json")
    bench = sub.add_parser("run", help="прогнать режимы и напечатать таблицу")
    bench.add_argument("--out", required=True, help="куда записать результаты в JSON")
    bench.add_argument("--checker-model", default="qwen3.5:9b")
    # С рассуждениями по умолчанию qwen3.5:9b тратила ответ на них и возвращала пустой content.
    bench.add_argument("--reasoning-effort", default="none")
    bench.add_argument("--llm-sample", type=int, default=60, help="размер подвыборки для LLM")
    bench.add_argument("--seed", type=int, default=7, help="seed подвыборки для LLM")
    bench.add_argument("--grey-zone", type=float, default=0.05)
    bench.add_argument("--modes", nargs="+", default=["llm", "both"], choices=["llm", "both"])
    bench.add_argument("--timeout", type=float, default=900.0, help="таймаут запроса, с")
    args = parser.parse_args(argv)
    if args.command == "build":
        rendered = render_cases(build_cases())
        if args.check:
            current = CASES.read_text(encoding="utf-8") if CASES.exists() else ""
            if rendered != current:
                log("cases.json устарел: выполните build")
                return 1
            return 0
        CASES.write_text(rendered, encoding="utf-8")
        log(f"записано вариантов: {len(json.loads(rendered))}")
        return 0
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
