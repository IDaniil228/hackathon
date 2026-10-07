"""Бенчмарк проверки ситуации эссе: режимы llm, embeddings, both и nli на размеченном наборе.

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
    uv run python scripts/essay_check_bench.py quotes --out quotes.json  # смысл цитат
    uv run --group nli python scripts/essay_check_bench.py nli --out nli.json \
        --llm-results tests/generation/bench/results/qwen3.5-9b-full.json  # модели NLI

Пороги эмбеддингов и NLI подбираются на половине эссе (tune) и меряется на другой половине (test):
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
from taskgen.config import ChatRole, LLMSettings, Settings
from taskgen.contracts import ChatMessage, TemplateSpec
from taskgen.evaluation.evidence import EvidenceOptions, split_sentences
from taskgen.evaluation.quotes import EmbeddingCache, QuoteRelevance
from taskgen.generation.essay import (
    EssayCheckOptions,
    EssayScenario,
    check_essay_scenario,
    embedding_scores,
    filled_facts,
)
from taskgen.generation.instance import checker_evidence
from taskgen.llm import ProviderRouter

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
    evidence: EvidenceOptions,
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
        evidence_options=evidence,
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

    base = Settings().llm.with_timeout(args.timeout)
    checker = ChatRole(model=args.checker_model, reasoning_effort=args.reasoning_effort)
    settings = LLMSettings.model_validate({**dict(base), "checker": checker})
    client = ProviderRouter.from_settings(settings, roles=("checker", "embedding"))
    llm = CountingLLM(client)
    # Способ доказательства проверяльщика — из настроек, например
    # TASKGEN_GENERATION__ESSAY_CHECK_EVIDENCE=spans.
    evidence = checker_evidence(Settings().generation)
    log(
        f"эмбеддер {settings.embedding.model}, проверяльщик {settings.checker.model}, "
        f"доказательство {evidence}"
    )

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
        f"{args.checker_model}|{args.reasoning_effort}|{evidence}"
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
                evidence,
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


# --- Смысл цитат ------------------------------------------------------------------------------
#
# Пары "текст пункта или факта — цитата" размечены по построению:
#
# - rubric/honest: цитата из правильного ответа на этот пункт, tests/generation/bench/quotes
# - rubric/other-item: честная цитата к другому пункту той же рубрики
# - rubric/generic: самоутверждение без темы задания, quotes/generic.yaml
# - rubric/echo: самоутверждение, пересказывающее видимые кандидату вопросы
# - rubric/aware: самоутверждение, которое называет понятие из пункта
# - fact/carrier: факт и предложение хорошей ситуации, которое его несёт
# - fact/other: факт и другое предложение той же ситуации
# - fact/negation: факт и предложение с его отрицанием
#
# Честный класс в каждой группе один: honest и carrier. Порог годится, если доля честных
# цитат ниже него около нуля.

QUOTES = BENCH / "quotes"
HONEST = {"rubric": "honest", "fact": "carrier"}


def quote_pairs(
    quotes_dir: Path = QUOTES, essays_dir: Path = BENCH / "essays"
) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    generic = yaml.safe_load((quotes_dir / "generic.yaml").read_text(encoding="utf-8"))["claims"]
    for source in sorted(quotes_dir.glob("*-essay.yaml")):
        pairs.extend(_rubric_pairs(yaml.safe_load(source.read_text(encoding="utf-8")), generic))
    for source in sorted(essays_dir.glob("*.yaml")):
        pairs.extend(_fact_pairs(yaml.safe_load(source.read_text(encoding="utf-8"))))
    return pairs


def _rubric_pairs(data: dict[str, Any], generic: list[str]) -> list[dict[str, Any]]:
    spec = load_spec(data["template"])
    items = {item.id: item.text for item in spec.rubric}
    honest, aware = data["honest"], data["claims"]["aware"]
    _require(set(honest) == set(items), f"{spec.name}: честные цитаты не по всем пунктам")
    _require(set(aware) == set(items), f"{spec.name}: aware не по всем пунктам")
    _require(all(honest.values()), f"{spec.name}: у пункта нет честных цитат")
    pairs: list[dict[str, Any]] = []

    def pair(kind: str, item_id: str, quote: str) -> None:
        pairs.append(
            {
                "group": "rubric",
                "kind": kind,
                "essay": spec.name,
                "target": item_id,
                "reference": items[item_id],
                "quotes": [quote],
            }
        )

    for item_id in items:
        for quote in honest[item_id]:
            pair("honest", item_id, quote)
        for other, quotes in honest.items():
            for quote in quotes if other != item_id else []:
                pair("other-item", item_id, quote)
        for quote in data["claims"]["echo"]:
            pair("echo", item_id, quote)
        for quote in generic:
            pair("generic", item_id, quote)
        pair("aware", item_id, aware[item_id])
    return pairs


def _fact_pairs(data: dict[str, Any]) -> list[dict[str, Any]]:
    spec = load_spec(data["template"])
    pairs: list[dict[str, Any]] = []
    texts: dict[str, dict[str, str]] = {}

    def pair(kind: str, fact_id: str, sid: str, sentence: str) -> None:
        pairs.append(
            {
                "group": "fact",
                "kind": kind,
                "essay": spec.name,
                "target": fact_id,
                "reference": texts[sid][fact_id],
                "quotes": [sentence],
            }
        )

    for good in data["situations"]:
        sid = good["id"]
        texts[sid] = {fact.id: fact.text for fact in filled_facts(spec, good["params"])}
        for unit in good["units"]:
            for fact_id in texts[sid]:
                pair("carrier" if fact_id in unit["facts"] else "other", fact_id, sid, unit["text"])
    for negation in data.get("negations") or []:
        pair("negation", negation["fact"], negation["situation"], negation["text"])
    return pairs


def quote_report(pairs: list[dict[str, Any]], scores: list[float]) -> dict[str, Any]:
    """Распределение честных сходств и доля каждого класса по ту сторону порога.

    Для честного класса доля — сколько отсечено (ниже порога), для остальных — сколько
    пропущено (не ниже порога). safe_threshold — наибольший порог сетки без потерь честных.
    """
    report: dict[str, Any] = {}
    thresholds = [round(0.30 + 0.01 * step, 2) for step in range(41)]
    for group, honest_kind in HONEST.items():
        by_kind: dict[str, list[float]] = defaultdict(list)
        for item, score in zip(pairs, scores, strict=True):
            if item["group"] == group:
                by_kind[item["kind"]].append(score)
        honest = sorted(by_kind[honest_kind])
        shares = {
            str(t): {
                kind: _beyond(values, t, cut=kind == honest_kind)
                for kind, values in by_kind.items()
            }
            for t in thresholds
        }
        safe = max(t for t in thresholds if shares[str(t)][honest_kind] == 0.0)
        report[group] = {
            "counts": {kind: len(values) for kind, values in by_kind.items()},
            "honest_quantiles": {
                "min": honest[0],
                "p5": honest[len(honest) // 20],
                "median": honest[len(honest) // 2],
            },
            "safe_threshold": safe,
            "shares": shares,
        }
    return report


def _beyond(values: list[float], threshold: float, *, cut: bool) -> float:
    """cut: доля ниже порога, то есть отсечённых; иначе доля пропущенных."""
    hits = sum((v < threshold) if cut else (v >= threshold) for v in values)
    return hits / len(values)


def quote_table(report: dict[str, Any], marks: list[float]) -> str:
    lines: list[str] = []
    for group, data in report.items():
        honest_kind = HONEST[group]
        kinds = [honest_kind] + sorted(k for k in data["counts"] if k != honest_kind)
        quantiles = ", ".join(f"{k} {v:.3f}" for k, v in data["honest_quantiles"].items())
        lines.append(
            f"\n{group}: пар {data['counts']}; честные: {quantiles}; "
            f"наибольший порог без потерь честных {data['safe_threshold']}"
        )
        lines.append(
            "| порог | "
            + " | ".join(f"{k} {'отсечено' if k == honest_kind else 'пропущено'}" for k in kinds)
            + " |"
        )
        lines.append("|" + "---|" * (len(kinds) + 1))
        for mark in sorted({*marks, data["safe_threshold"]}):
            shares = data["shares"][str(round(mark, 2))]
            lines.append(f"| {mark:.2f} | " + " | ".join(f"{shares[k]:.1%}" for k in kinds) + " |")
    return "\n".join(lines)


async def run_quotes(args: argparse.Namespace) -> int:
    pairs = quote_pairs()
    client = ProviderRouter.from_settings(Settings().llm.with_timeout(600.0), roles=("embedding",))
    log(f"пар {len(pairs)}, эмбеддер {client.model_name}")
    # Сходство считает тот же код, что и сервис: склейка цитат и векторы пунктов из кэша.
    relevance = QuoteRelevance(threshold=0.0, embedder=client)
    scores: list[float] = []
    batch = 128
    for start in range(0, len(pairs), batch):
        chunk = pairs[start : start + batch]
        scores += await relevance.similarities([(p["reference"], p["quotes"]) for p in chunk])
    await client.aclose()
    report = quote_report(pairs, scores)
    table_text = quote_table(report, args.marks)
    print(f"Эмбеддер {client.model_name}" + table_text)
    Path(args.out).write_text(
        json.dumps(
            {
                "embedding_model": client.model_name,
                "report": report,
                "table": table_text,
                # Пары целиком пересчитываются прогоном; ближе всего к порогу — худшие честные.
                "lowest_honest": {
                    group: sorted(
                        (
                            {"essay": p["essay"], "target": p["target"], "score": round(s, 4)}
                            for p, s in zip(pairs, scores, strict=True)
                            if p["group"] == group and p["kind"] == kind
                        ),
                        key=lambda row: row["score"],
                    )[:10]
                    for group, kind in HONEST.items()
                },
            },
            ensure_ascii=False,
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


# --- NLI ------------------------------------------------------------------------------------
#
# Режим nli считает только модель NLI и не зовёт ни эмбеддер, ни LLM. С ним сравниваются
# строки embeddings и llm из сохранённых прогонов команды run (--llm-results): LLM на тех же
# вариантах не перезапускается. Запрещённые фразы NLI ищет только как эксперимент
# (PHRASE_HYPOTHESIS), а в комбинациях их решают подстрока и сохранённые вердикты LLM.

NLI_MODELS = {
    "rubert-base": ("cointegrated/rubert-base-cased-nli-threeway", "onnx/model.onnx"),
    "mdeberta": ("MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7", "onnx/model.onnx"),
    "mdeberta-int8": (
        "MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7",
        "onnx/model_quantized.onnx",
    ),
}
# None — без вето: факт решает только порог entailment.
CONTRADICTION_GRID: tuple[float | None, ...] = (None, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99)
WINDOW_NAMES = {0: "вся ситуация", 1: "предложения", 2: "предложения и пары"}
PHRASE_HYPOTHESIS = "В тексте прямо или другими словами названо понятие «{phrase}»."


def probability_grid() -> list[float]:
    return [round(0.01 * step, 2) for step in range(1, 100)]


class MemoNLI:
    """Модель NLI, которая не считает одну пару дважды: окно 2 включает все пары окна 1."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.name = inner.name
        self.memo: dict[tuple[str, str], Any] = {}

    async def infer(self, pairs: list[tuple[str, str]]) -> list[Any]:
        todo = [pair for pair in dict.fromkeys(pairs) if pair not in self.memo]
        self.memo.update(zip(todo, await self.inner.infer(todo), strict=True))
        return [self.memo[pair] for pair in pairs]


async def nli_scores(
    model: Any, cases, windows: list[int], phrases: bool, partial: Path, config: str
) -> dict[str, Any]:
    """Исходы NLI для каждого варианта; время — на одну ситуацию, как у сервиса.

    Время окна — время всех его пар. Окна 1 и 2 считаются подряд с общей памятью пар, и
    время окна 2 — это время окна 1 плюс время новых пар. Окно 0, вся ситуация одной
    посылкой, считается отдельно. Строки пишутся в partial сразу: обрыв долгого прогона на
    CPU не теряет посчитанное, а новое окно досчитывается к старым строкам.
    """
    from taskgen.generation.nli import fact_inferences

    scores: dict[str, Any] = {
        row["case_id"]: row["entry"] for row in load_partial(partial) if row.get("config") == config
    }
    if scores:
        log(f"из {partial} взято вариантов: {len(scores)}")
    for number, case in enumerate(cases, start=1):
        spec = load_spec(case["template"])
        entry: dict[str, Any] = scores.get(case["id"]) or {
            "facts": {},
            "seconds": {},
            "literal": find_phrases(f"{case['title']}\n{case['situation']}", spec.forbidden),
        }
        todo = [window for window in windows if str(window) not in entry["facts"]]
        if not todo and (not phrases or "phrases" in entry):
            continue
        facts = filled_facts(spec, case["params"])
        sentences = split_sentences(case["situation"])
        memo = MemoNLI(model)
        elapsed = 0.0
        for window in sorted(todo, key=lambda w: w == 0):
            if window == 0:
                memo, elapsed = MemoNLI(model), 0.0
            started = time.perf_counter()
            result = await fact_inferences(memo, facts, sentences, window)
            elapsed += time.perf_counter() - started
            entry["seconds"][str(window)] = elapsed
            entry["facts"][str(window)] = {
                fid: [match.entailment, match.contradiction] for fid, match in result.items()
            }
        if phrases and "phrases" not in entry:
            units = list(dict.fromkeys([case["title"].strip(), *sentences]))
            hypotheses = [PHRASE_HYPOTHESIS.format(phrase=p) for p in spec.forbidden]
            started = time.perf_counter()
            inferred = await model.infer([(u, h) for h in hypotheses for u in units])
            entry["phrase_seconds"] = time.perf_counter() - started
            entry["phrases"] = {
                phrase: max(
                    row.entailment for row in inferred[i * len(units) : (i + 1) * len(units)]
                )
                for i, phrase in enumerate(spec.forbidden)
            }
        scores[case["id"]] = entry
        with partial.open("a", encoding="utf-8") as stream:
            row = {"config": config, "case_id": case["id"], "entry": entry}
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        if number % 20 == 0:
            log(f"[{number}/{len(cases)}] NLI посчитан")
    return scores


def nli_missing(entry: dict[str, Any], window: int, threshold: float, veto: float | None):
    return [
        fid
        for fid, (entail, contra) in entry["facts"][str(window)].items()
        if (veto is not None and contra >= veto) or entail < threshold
    ]


def nli_found(entry: dict[str, Any], threshold: float) -> list[str]:
    """Фразы, найденные подстрокой или гипотезой PHRASE_HYPOTHESIS не ниже порога."""
    return [
        phrase
        for phrase, value in entry["phrases"].items()
        if phrase in entry["literal"] or value >= threshold
    ]


def _row(cid: str, missing, found, seconds: float = 0.0, calls: int = 0) -> dict[str, Any]:
    return {
        "case_id": cid,
        "missing_facts": list(missing),
        "forbidden_found": list(found),
        "seconds": seconds,
        "llm_calls": calls,
    }


def tune_nli(scores, by_id, tune_ids, window: int) -> dict[str, Any]:
    """Порог entailment и порог вето с наибольшим F1 фактов на tune; при равенстве — без вето
    или с меньшим вето, так как CONTRADICTION_GRID идёт от None по возрастанию."""
    best: dict[str, Any] | None = None
    for veto in CONTRADICTION_GRID:

        def f1(threshold: float, veto: float | None = veto) -> float:
            rows = [
                _row(cid, nli_missing(scores[cid], window, threshold, veto), []) for cid in tune_ids
            ]
            return evaluate(rows, by_id)["fact"][2]

        threshold, value = tune_threshold(probability_grid(), f1)
        if best is None or value > best["f1"] + 1e-9:
            best = {"window": window, "threshold": threshold, "veto": veto, "f1": value}
    assert best is not None
    return best


def load_saved(paths: list[str], by_id: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Строки embeddings и llm сохранённых прогонов; вариант не из cases.json — ошибка."""
    saved: dict[str, dict[str, Any]] = {}
    for path in paths:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        unknown = {row["case_id"] for row in data["rows"]} - set(by_id)
        _require(not unknown, f"{path}: варианты не из cases.json: {sorted(unknown)[:3]}")
        modes: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        for row in data["rows"]:
            modes[row["mode"]][row["case_id"]] = row
        saved[Path(path).stem] = {"sample": data["sample"], "modes": modes}
    return saved


async def run_nli(args: argparse.Namespace) -> int:
    from taskgen.generation.nli import OnnxNLI

    cases = json.loads(CASES.read_text(encoding="utf-8"))
    if render_cases(build_cases()) != CASES.read_text(encoding="utf-8"):
        log("cases.json не совпадает с источниками: выполните build")
        return 1
    by_id = {case["id"]: case for case in cases}
    tune, test = split_essays(cases)
    tune_ids = [case["id"] for case in cases if case["essay"] in tune]
    test_ids = {case["id"] for case in cases if case["essay"] in test}
    saved = load_saved(args.llm_results, by_id)
    windows = sorted(set(args.windows))

    output: list[str] = [f"tune: {', '.join(tune)}", f"test: {', '.join(test)}"]
    tuned: dict[str, Any] = {}
    all_rows: dict[str, list[dict[str, Any]]] = {}
    raw: dict[str, Any] = {}
    for name in args.models:
        repo, onnx_file = NLI_MODELS[name]
        model = OnnxNLI(repo, onnx_file, threads=args.threads)
        started = time.perf_counter()
        await model.infer([("Проверка.", "Проверка.")])
        log(f"{name}: модель загружена за {time.perf_counter() - started:.1f} с")
        phrases = name in args.phrase_models
        config = f"{repo}|{onnx_file}|{PHRASE_HYPOTHESIS}"
        partial = Path(f"{args.out}.partial.jsonl")
        scores = await nli_scores(model, cases, windows, phrases, partial, config)
        raw[name] = scores
        for window in windows:
            choice = tune_nli(scores, by_id, tune_ids, window)
            label = f"{name}, {WINDOW_NAMES[window]}"
            tuned[label] = choice
            log(f"{label}: {choice}")
            all_rows[label] = [
                _row(
                    cid,
                    nli_missing(scores[cid], window, choice["threshold"], choice["veto"]),
                    scores[cid]["literal"],
                    scores[cid]["seconds"][str(window)],
                )
                for cid in by_id
            ]
        if phrases:

            def phrase_f1(threshold: float, scores=scores) -> float:
                rows = [_row(cid, [], nli_found(scores[cid], threshold)) for cid in tune_ids]
                return evaluate(rows, by_id)["forbidden"][2]

            threshold, value = tune_threshold(probability_grid(), phrase_f1)
            label = f"{name}, фразы NLI"
            tuned[label] = {"threshold": threshold, "f1": value}
            log(f"{label}: порог {threshold}, F1 на tune {value:.3f}")
            all_rows[label] = [
                _row(cid, [], nli_found(scores[cid], threshold), scores[cid]["phrase_seconds"])
                for cid in by_id
            ]

    def subset(rows, ids):
        return [row for row in rows if row["case_id"] in ids]

    fact_labels = [label for label in all_rows if "фразы" not in label]
    output.append(
        table(
            "NLI на tune и test: факты моделью, запрещённое подстрокой",
            [
                (f"{label}, {half}", evaluate(subset(all_rows[label], ids), by_id))
                for label in fact_labels
                for half, ids in (("tune", set(tune_ids)), ("test", test_ids))
            ],
        )
    )
    phrase_labels = [label for label in all_rows if "фразы" in label]
    if phrase_labels:
        output.append(
            table(
                "Эксперимент: запрещённые фразы через гипотезу NLI, test",
                [
                    (label, evaluate(subset(all_rows[label], test_ids), by_id))
                    for label in phrase_labels
                ],
            )
        )

    for run_name, run in saved.items():
        llm_rows = run["modes"]["llm"]
        for title, ids in (
            ("все варианты прогона", set(llm_rows)),
            ("варианты прогона ∩ test", set(llm_rows) & test_ids),
        ):
            results = [
                (
                    "embeddings",
                    evaluate(subset(list(run["modes"]["embeddings"].values()), ids), by_id),
                ),
                ("llm", evaluate(subset(list(llm_rows.values()), ids), by_id)),
            ]
            for label in fact_labels:
                nli_rows = {row["case_id"]: row for row in all_rows[label]}
                results.append(
                    (f"{label} + подстрока", evaluate(subset(list(nli_rows.values()), ids), by_id))
                )
                combined = []
                for cid in sorted(ids):
                    nli_row = nli_rows[cid]
                    # LLM зовётся, только если NLI и подстрока ситуацию пропустили: иначе
                    # она уже отклонена. Вердикты фраз берутся из сохранённого прогона llm.
                    if nli_row["missing_facts"] or nli_row["forbidden_found"]:
                        combined.append(nli_row)
                        continue
                    llm = llm_rows[cid]
                    combined.append(
                        _row(
                            cid,
                            nli_row["missing_facts"],
                            sorted(set(nli_row["forbidden_found"]) | set(llm["forbidden_found"])),
                            nli_row["seconds"] + llm["seconds"],
                            llm["llm_calls"],
                        )
                    )
                results.append((f"{label} + LLM для фраз", evaluate(combined, by_id)))
            output.append(table(f"Сравнение с {run_name}: {title}", results))

    report = "\n".join(output)
    print(report)
    Path(args.out).write_text(
        json.dumps(
            {
                "models": {name: NLI_MODELS[name] for name in args.models},
                "phrase_hypothesis": PHRASE_HYPOTHESIS,
                "phrase_models": args.phrase_models,
                "tune": tune,
                "test": test,
                "tuned": tuned,
                "scores": raw,
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
    bench.add_argument(
        "--checker-model", required=True, help="модель LLM-проверяльщика: провайдер/модель"
    )
    # С рассуждениями по умолчанию локальная модель тратила на них весь ответ и возвращала
    # пустой content, поэтому по умолчанию рассуждения выключены.
    bench.add_argument("--reasoning-effort", default="none")
    bench.add_argument("--llm-sample", type=int, default=60, help="размер подвыборки для LLM")
    bench.add_argument("--seed", type=int, default=7, help="seed подвыборки для LLM")
    bench.add_argument("--grey-zone", type=float, default=0.05)
    bench.add_argument("--modes", nargs="+", default=["llm", "both"], choices=["llm", "both"])
    bench.add_argument("--timeout", type=float, default=900.0, help="таймаут запроса, с")
    quotes = sub.add_parser("quotes", help="замерить сходство цитат с пунктами и фактами")
    quotes.add_argument("--out", required=True, help="куда записать результаты в JSON")
    quotes.add_argument(
        "--marks",
        nargs="+",
        type=float,
        default=[0.35, 0.40, 0.45, 0.50, 0.55, 0.60],
        help="пороги для таблицы, кроме наибольшего без потерь честных",
    )
    nli = sub.add_parser("nli", help="проверить факты моделями NLI и сравнить с сохранёнными")
    nli.add_argument("--out", required=True, help="куда записать результаты в JSON")
    nli.add_argument("--models", nargs="+", default=["rubert-base", "mdeberta"], choices=NLI_MODELS)
    nli.add_argument(
        "--windows",
        nargs="+",
        type=int,
        default=[0, 1, 2],
        choices=[0, 1, 2],
        help="посылки: 1 — предложение, 2 — ещё и пары соседних, 0 — вся ситуация",
    )
    nli.add_argument(
        "--llm-results",
        nargs="+",
        default=[],
        help="сохранённые результаты команды run, с которыми сравнивать",
    )
    nli.add_argument(
        "--phrase-models",
        nargs="*",
        default=[],
        choices=NLI_MODELS,
        help="модели, на которых проверить запрещённые фразы гипотезой NLI",
    )
    nli.add_argument("--threads", type=int, default=None, help="потоков onnxruntime")
    args = parser.parse_args(argv)
    if args.command == "nli":
        return asyncio.run(run_nli(args))
    if args.command == "quotes":
        return asyncio.run(run_quotes(args))
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
