"""Правдоподобные ошибки в эталонах банка шаблонов.

Мутант — замена фрагмента эталона, которая воспроизводит типичную ошибку кандидата. Тесты
шаблона обязаны поймать каждого мутанта хотя бы на одном кейсе. Если фрагмент исчез из
эталона, шлюз падает: мутант нужно переписать под новый эталон, а не удалить.
"""

MUTANTS: dict[str, list[tuple[str, str, str]]] = {
    "algo-pairs-sum-at-most": [
        ("строгое сравнение с порогом", "<= limit:", "< limit:"),
        ("пары считаются по одной", "count += right - left", "count += 1"),
    ],
    "algo-top-k-ranking-cpp": [
        ("ничьи не упорядочены по имени", "return names[a] < names[b];", "return false;"),
        ("отрицательное k не отсечено", "std::max(k, 0)", "k"),
    ],
    "algo-grid-weighted-path": [
        ("обход в ширину без весов", 'step = slow_cost if grid[nr][nc] == "~" else 1', "step = 1"),
    ],
    "algo-max-meetings-cpp": [
        ("касание концов считается пересечением", "start >= free_from", "start > free_from"),
        ("жадность по началу заявки", "return a.second < b.second;", "return a.first < b.first;"),
    ],
    "algo-cheapest-route-cpp": [
        (
            "ослабление на месте нарушает лимит рёбер",
            "next[e[1]] = std::min(next[e[1]], best[e[0]] + e[2]);",
            "best[e[1]] = std::min(best[e[1]], best[e[0]] + e[2]);",
        ),
        ("число рёбер не минимально", "answer_edges = round;", "answer_edges = max_stops + 1;"),
    ],
    "algo-weighted-jobs": [
        ("работа на границе перерыва отброшена", "bisect.bisect_right(", "bisect.bisect_left("),
        ("перерыв не учтён", "end + gap for", "end for"),
    ],
    "ml-fbeta-score": [
        ("F1 вместо F-beta", "b2 * fn + fp", "fn + fp"),
    ],
    "ml-standardize-features": [
        ("ddof проигнорирован", "ddof=ddof", "ddof=0"),
        ("постоянный столбец не обнулён", "scaled[:, constant] = 0.0", "pass"),
    ],
    "ml-logreg-gradient-step": [
        ("нет L2 в градиенте весов", "+ l2 * w", ""),
        (
            "свободный член регуляризуется",
            "grad_b = error.mean()",
            "grad_b = error.mean() + l2 * b",
        ),
    ],
    "ml-kmeans-step": [
        (
            "ничья уходит к последнему центру",
            "labels = squared.argmin(axis=1)",
            "labels = len(centroids) - 1 - squared[:, ::-1].argmin(axis=1)",
        ),
        (
            "пустой кластер обнуляется",
            "updated[filled] = sums[filled] / counts[filled, None]",
            "updated = sums / np.maximum(counts, 1)[:, None]",
        ),
    ],
    "ml-softmax-cross-entropy": [
        (
            "сглаживание только по неверным классам",
            "np.full_like(logits, smoothing / classes)",
            "np.full_like(logits, smoothing / max(classes - 1, 1))",
        ),
        ("среднее по всему батчу", "count = rows.size", "count = len(targets)"),
    ],
    "ml-batchnorm-backward": [
        ("несмещённая дисперсия", "x.var(axis=0)", "x.var(axis=0, ddof=1)"),
        (
            "нет вкладов через среднее и дисперсию",
            "n * dx_hat - dx_hat.sum(axis=0) - x_hat * (dx_hat * x_hat).sum(axis=0)",
            "n * dx_hat",
        ),
    ],
    "backend-validate-signup": [
        ("bool принят за int", "and not isinstance(value, bool)", ""),
        (
            "буква не только латинская",
            'LETTER = re.compile(r"[A-Za-z]")',
            r'LETTER = re.compile(r"[^\W\d_]")',
        ),
    ],
    "backend-paginate": [
        (
            "isdigit вместо цифр 0–9",
            'if raw and all(ch in "0123456789" for ch in raw):',
            "if raw.strip().isdigit():",
        ),
        (
            "страница меньше 1 не поднята",
            "page = max(1, _number(page, 1))",
            "page = _number(page, 1)",
        ),
    ],
    "backend-sliding-rate-limiter": [
        ("закрытая левая граница окна", "recent[0] <= t - window", "recent[0] < t - window"),
        (
            "отклонённые запросы расходуют лимит",
            "if allowed:\n            recent.append(t)",
            "recent.append(t)",
        ),
    ],
    "backend-idempotency-keys": [
        ("запись живёт ровно ttl включительно", "t - record[0] < ttl", "t - record[0] <= ttl"),
        ("ошибки сервера сохраняются", "if key and status < 500:", "if key:"),
    ],
    "backend-ttl-lru-cache": [
        ("просрочка со следующего момента", "deadlines[0][0] <= now", "deadlines[0][0] < now"),
        (
            "устаревший срок в куче удаляет новую запись",
            "if key in entries and entries[key][1] == expires_at:",
            "if key in entries:",
        ),
        (
            "обновление живого ключа вытесняет запись",
            "if key not in entries and len(entries) >= capacity:",
            "if len(entries) >= capacity:",
        ),
    ],
    "backend-latency-report": [
        (
            "перцентиль через round",
            "rank = -(-percentile * len(durations) // 100)",
            "rank = max(1, round(percentile * len(durations) / 100))",
        ),
        (
            "isdigit вместо цифр 0–9",
            'return bool(text) and all(ch in "0123456789" for ch in text)',
            "return text.isdigit()",
        ),
    ],
}
