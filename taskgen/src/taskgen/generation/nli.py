"""Проверка фактов ситуации эссе моделью NLI (natural language inference).

Модель NLI получает пару "посылка, гипотеза" и даёт вероятности трёх исходов: посылка
влечёт гипотезу (entailment), противоречит ей (contradiction) или ни то ни другое
(neutral). Посылка — предложение ситуации или окно из соседних предложений, гипотеза —
факт шаблона. В отличие от косинусного сходства эмбеддингов, отрицание факта даёт
contradiction, а не высокий балл.

Модель работает в процессе через onnxruntime. Зависимости стоят в необязательной группе
uv nli (uv sync --group nli), поэтому основной сервис и CI их не тянут. Без группы
OnnxNLI отвечает NLIUnavailableError с командой установки.

Сервис проверку NLI не использует: модуль нужен бенчмарку scripts/essay_check_bench.py nli.
Почему NLI не стал режимом проверки, сказано в docs/taskgen/validation.md.
"""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from taskgen.contracts import ScenarioFact


class NLIUnavailableError(RuntimeError):
    """Зависимости NLI не установлены или модель не загрузилась."""


@dataclass(frozen=True)
class Inference:
    """Вероятности исходов для одной пары; neutral — остаток до единицы."""

    entailment: float
    contradiction: float


class NLIModel(Protocol):
    name: str

    async def infer(self, pairs: list[tuple[str, str]]) -> list[Inference]:
        """Исходы для пар (посылка, гипотеза) в том же порядке."""
        ...


INSTALL_HINT = "установите группу зависимостей: cd taskgen && uv sync --group nli"


def import_runtime() -> tuple[Any, Any, Any, Any]:
    """Модули numpy и onnxruntime, hf_hub_download и Tokenizer; без группы nli — ошибка."""
    try:
        import numpy
        import onnxruntime
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer
    except ImportError as error:
        raise NLIUnavailableError(
            f"проверке NLI не хватает пакета {error.name}: {INSTALL_HINT}"
        ) from error
    return numpy, onnxruntime, hf_hub_download, Tokenizer


class OnnxNLI:
    """Модель NLI с Hugging Face Hub в формате ONNX: токенизатор tokenizer.json и onnx-файл.

    Порядок меток модели берётся из её config.json (id2label): у разных моделей он разный.
    Файлы скачиваются в кэш Hugging Face при первом вызове infer, а не в конструкторе:
    пока пар нет, модель не занимает ни время, ни память.
    """

    def __init__(
        self,
        repo: str,
        onnx_file: str = "onnx/model.onnx",
        *,
        revision: str | None = None,
        batch_size: int = 16,
        max_length: int = 512,
        threads: int | None = None,
    ) -> None:
        self.name = repo
        self.repo = repo
        self.onnx_file = onnx_file
        self.revision = revision
        self.batch_size = batch_size
        self.max_length = max_length
        self.threads = threads
        self._loaded: tuple[Any, Any, Any, list[str], int, int] | None = None
        self._lock = threading.Lock()

    def _load(self) -> tuple[Any, Any, Any, list[str], int, int]:
        with self._lock:
            if self._loaded is None:
                self._loaded = self._load_files()
            return self._loaded

    def _load_files(self) -> tuple[Any, Any, Any, list[str], int, int]:
        numpy, onnxruntime, hf_hub_download, tokenizer_class = import_runtime()

        def fetch(filename: str) -> str:
            return hf_hub_download(self.repo, filename, revision=self.revision)

        try:
            config = json.loads(Path(fetch("config.json")).read_text(encoding="utf-8"))
            tokenizer = tokenizer_class.from_file(fetch("tokenizer.json"))
            options = onnxruntime.SessionOptions()
            if self.threads is not None:
                options.intra_op_num_threads = self.threads
            session = onnxruntime.InferenceSession(
                fetch(self.onnx_file), options, providers=["CPUExecutionProvider"]
            )
        except Exception as error:
            raise NLIUnavailableError(f"модель NLI {self.repo} не загрузилась: {error}") from error
        labels = {str(label).lower(): int(index) for index, label in config["id2label"].items()}
        if not {"entailment", "contradiction"} <= set(labels):
            raise NLIUnavailableError(
                f"у модели {self.repo} нет меток entailment и contradiction: {sorted(labels)}"
            )
        # longest_first режет длинную посылку, например блок кода, и никогда не падает
        # на короткой паре, в отличие от only_first.
        tokenizer.enable_truncation(self.max_length, strategy="longest_first")
        if tokenizer.padding is None:
            tokenizer.enable_padding()
        inputs = [item.name for item in session.get_inputs()]
        return numpy, tokenizer, session, inputs, labels["entailment"], labels["contradiction"]

    def _infer_sync(self, pairs: list[tuple[str, str]]) -> list[Inference]:
        numpy, tokenizer, session, inputs, entail, contra = self._load()
        result: list[Inference] = []
        for start in range(0, len(pairs), self.batch_size):
            encoded = tokenizer.encode_batch(pairs[start : start + self.batch_size])
            feed = {
                "input_ids": [item.ids for item in encoded],
                "attention_mask": [item.attention_mask for item in encoded],
                "token_type_ids": [item.type_ids for item in encoded],
            }
            logits = session.run(
                None, {key: numpy.array(feed[key], dtype=numpy.int64) for key in inputs}
            )[0]
            shifted = numpy.exp(logits - logits.max(axis=1, keepdims=True))
            probs = shifted / shifted.sum(axis=1, keepdims=True)
            result.extend(Inference(float(row[entail]), float(row[contra])) for row in probs)
        return result

    async def infer(self, pairs: list[tuple[str, str]]) -> list[Inference]:
        if not pairs:
            return []
        return await asyncio.to_thread(self._infer_sync, pairs)


def premise_units(sentences: list[str], window: int) -> list[str]:
    """Посылки: каждое предложение и, при window = 2, каждая пара соседних предложений.

    Окно нужно факту, который ситуация разнесла по двум предложениям: ни одно из них
    по отдельности его не влечёт. window = 0 — вся ситуация одной посылкой; длинную
    ситуацию токенизатор обрезает до max_length модели.
    """
    if window == 0:
        return [" ".join(sentences)] if sentences else []
    units = list(sentences)
    if window >= 2:
        units += [" ".join(sentences[i : i + window]) for i in range(len(sentences) - 1)]
    return list(dict.fromkeys(units))


@dataclass(frozen=True)
class FactInference:
    """Наибольшие entailment и contradiction факта по посылкам и посылка с entailment."""

    entailment: float
    contradiction: float
    unit: str


async def fact_inferences(
    model: NLIModel, facts: list[ScenarioFact], sentences: list[str], window: int = 1
) -> dict[str, FactInference]:
    """Для каждого факта — наибольшие entailment и contradiction по посылкам ситуации."""
    units = premise_units(sentences, window)
    pairs = [(unit, fact.text) for fact in facts for unit in units]
    scored = await model.infer(pairs)
    result: dict[str, FactInference] = {}
    for number, fact in enumerate(facts):
        rows = scored[number * len(units) : (number + 1) * len(units)]
        if not rows:
            result[fact.id] = FactInference(0.0, 0.0, "")
            continue
        best = max(range(len(rows)), key=lambda index: rows[index].entailment)
        result[fact.id] = FactInference(
            entailment=rows[best].entailment,
            contradiction=max(row.contradiction for row in rows),
            unit=units[best],
        )
    return result


def fact_present(score: FactInference, threshold: float, contradiction: float | None) -> bool:
    """Факт есть, если его влечёт хотя бы одна посылка и ни одна ему не противоречит.

    contradiction=None выключает вето: решает только порог entailment.
    """
    if contradiction is not None and score.contradiction >= contradiction:
        return False
    return score.entailment >= threshold
