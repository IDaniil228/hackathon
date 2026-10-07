"""Окружение тестов сессий: приложение на настоящей БД с подделками LLM и песочницы.

Каждый World работает в своей области: отбор не видит шаблоны других тестов.
"""

from __future__ import annotations

import json
import re
import time
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from taskgen.bank.importer import TemplateDocument, import_documents
from taskgen.db import (
    Area,
    AreaSkill,
    CandidateSummary,
    PoolInstance,
    Task,
    Template,
    TestSession,
)
from tests.bank.factory import Unchecked
from tests.conftest import ADMIN_KEY
from tests.evaluation.fakes import FakeLLM, http_error
from tests.generation.fakes import GatedLLM, LocalSandbox, essay_reply, theme_text
from tests.generation.templates import (
    code_template_md,
    essay_template_md,
    quiz_template_md,
    solutions_section,
)

ADMIN = {"Authorization": f"Bearer {ADMIN_KEY}"}
ENTRYPOINT_IN_PROMPT = re.compile(r"функцию `(\w+)`")
ANSWER_IN_JUDGE = re.compile(r"<<<ОТВЕТ-\w+>>>\n(.*?)\n<<<КОНЕЦ", re.DOTALL)
BROKEN_REFERENCE = "def top_sum(nums, k):\n    return 0\n"


class ModelStub:
    """Отвечает за генератора, судью и summary; что именно спрошено, видно по схеме."""

    def __init__(self) -> None:
        self.down = False

    def __call__(self, messages, schema):
        if self.down:
            raise http_error(503)
        if (essay := essay_reply(messages, schema)) is not None:
            return essay
        properties = (schema or {}).get("properties", {})
        if "sentences" in properties:
            return json.dumps({"sentences": ["Кандидат решил задания."]}, ensure_ascii=False)
        if "items" in properties:
            # Провайдер без json_schema получает схему последним сообщением, после промпта.
            prompt = "\n".join(message.content for message in messages)
            answer = ANSWER_IN_JUDGE.search(prompt).group(1)
            quote = next(line for line in answer.splitlines() if line.strip())
            item = properties["items"]["items"]["properties"]
            ids = item["item_id"]["enum"]
            # Первый участок ответа — его первая непустая строка.
            evidence = {"spans": [1]} if "spans" in item else {"quotes": [quote]}
            items = [{"item_id": i, "verdict": "yes", **evidence} for i in ids]
            return json.dumps({"items": items}, ensure_ascii=False)
        prompt = messages[1].content
        match = ENTRYPOINT_IN_PROMPT.search(prompt)
        statement = f"Напишите функцию {match.group(1)}." if match else "Расскажите про GIL."
        # Честный писатель встраивает тему легенды, которую потом ищет проверяльщик.
        if theme := theme_text(prompt):
            statement += f" {theme}"
        return json.dumps({"title": "Задание", "statement": statement}, ensure_ascii=False)


# По названию проверка покрытия банка отличает области тестового мира от настоящих: прогон,
# прерванный до cleanup, оставляет такую область в общей БД.
TEST_AREA_TITLE = "Тестовая область"


class World:
    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.area = f"g{uuid.uuid4().hex[:12]}"
        self.candidate = f"cand-{uuid.uuid4().hex[:8]}"
        self.prefix = f"g{uuid.uuid4().hex[:10]}"
        self._run(self._create_area)

    @property
    def state(self):
        return self.client.app.state

    def _run(self, fn, *args):
        return self.client.portal.call(fn, *args)

    async def _create_area(self) -> None:
        async with self.state.sessionmaker() as db, db.begin():
            db.add(Area(code=self.area, title=TEST_AREA_TITLE))
            await db.flush()
            db.add(AreaSkill(area_code=self.area, skill_code="python"))

    def add_templates(self, *bodies: str) -> None:
        async def load():
            async with self.state.sessionmaker() as db, db.begin():
                docs = [TemplateDocument(f"t{i}.md", body) for i, body in enumerate(bodies)]
                # Тесты генерации кладут и заведомо сломанные шаблоны (broken), чтобы проверить
                # страховку при генерации; проверку импорта проверяют тесты банка.
                await import_documents(db, docs, check=Unchecked())

        self._run(load)

    def code(self, suffix: str = "code", **frontmatter) -> str:
        frontmatter.setdefault("skills", ["python"])
        return code_template_md(name=f"{self.prefix}-{suffix}", area=self.area, **frontmatter)

    def broken(self, suffix: str = "bad", **frontmatter) -> str:
        return self.code(
            suffix, sections={"Решения": solutions_section(BROKEN_REFERENCE)}, **frontmatter
        )

    def essay(self, suffix: str = "essay", **frontmatter) -> str:
        frontmatter.setdefault("skills", ["python"])
        return essay_template_md(name=f"{self.prefix}-{suffix}", area=self.area, **frontmatter)

    def quiz(self, suffix: str = "quiz", **frontmatter) -> str:
        frontmatter.setdefault("skills", ["python"])
        return quiz_template_md(name=f"{self.prefix}-{suffix}", area=self.area, **frontmatter)

    def gate(self, free: int) -> GatedLLM:
        """Пропустить free вызовов LLM, а остальные держать до gated.open()."""
        gated = GatedLLM(self.state.llm, free=free)
        self.state.llm = gated
        return gated

    def open(self, gated: GatedLLM) -> None:
        self._run(gated.open)

    def create(self, headers=ADMIN, **body):
        payload = {
            "candidate_ref": self.candidate,
            "area": self.area,
            "claimed_grade": "junior",
            "skills": ["python"],
        } | body
        return self.client.post("/sessions", json=payload, headers=headers)

    def wait(self) -> None:
        """Дождаться, пока фоновая генерация сессий и пула закончится."""
        self._run(self.state.generation_worker.wait_idle)

    def wait_task_ready(self, session_id, timeout: float = 30.0) -> Task:
        """Дождаться, пока фоновая генерация запишет хотя бы одно готовое задание сессии."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ready = [task for task in self.tasks(session_id) if task.status == "ready"]
            if ready:
                return ready[0]
            time.sleep(0.05)
        raise AssertionError(f"за {timeout} с ни одно задание сессии {session_id} не готово")

    def view(self, session_id, headers=ADMIN) -> dict:
        response = self.client.get(f"/sessions/{session_id}", headers=headers)
        assert response.status_code == 200, response.text
        return response.json()

    def create_ready(self, headers=ADMIN, **body) -> dict:
        response = self.create(headers=headers, **body)
        assert response.status_code == 201, response.text
        self.wait()
        return self.view(response.json()["id"], headers)

    def templates(self) -> dict[str, Template]:
        """Текущие шаблоны области по суффиксу имени."""

        async def load():
            async with self.state.sessionmaker() as db:
                query = select(Template).where(Template.area_code == self.area, Template.is_current)
                return list(await db.scalars(query))

        return {t.name.removeprefix(f"{self.prefix}-"): t for t in self._run(load)}

    def tasks(self, session_id, *where) -> list[Task]:
        async def load():
            async with self.state.sessionmaker() as db:
                query = (
                    select(Task)
                    .where(Task.session_id == session_id, *where)
                    .order_by(Task.position)
                )
                return list(await db.scalars(query))

        return self._run(load)

    def pool(self) -> list[PoolInstance]:
        async def load():
            async with self.state.sessionmaker() as db:
                query = (
                    select(PoolInstance)
                    .join(Template, PoolInstance.template_id == Template.id)
                    .where(Template.area_code == self.area)
                    .order_by(PoolInstance.created_at)
                )
                return list(await db.scalars(query))

        return self._run(load)

    def sessions(self, *where) -> list[TestSession]:
        async def load():
            async with self.state.sessionmaker() as db:
                query = select(TestSession).where(
                    TestSession.candidate_ref == self.candidate, *where
                )
                return list(await db.scalars(query))

        return self._run(load)

    def cleanup(self) -> None:
        async def drop():
            async with self.state.sessionmaker() as db, db.begin():
                await db.execute(
                    delete(TestSession).where(TestSession.candidate_ref == self.candidate)
                )
                await db.execute(delete(Template).where(Template.area_code == self.area))
                await db.execute(delete(AreaSkill).where(AreaSkill.area_code == self.area))
                await db.execute(delete(Area).where(Area.code == self.area))
                await db.execute(
                    delete(CandidateSummary).where(CandidateSummary.candidate_ref == self.candidate)
                )

        self._run(drop)


def fake_world(client: TestClient) -> World:
    client.portal.call(client.app.state.llm.aclose)
    client.app.state.model_stub = ModelStub()
    client.app.state.llm = FakeLLM(client.app.state.model_stub)
    client.app.state.sandbox = LocalSandbox()
    return World(client)
