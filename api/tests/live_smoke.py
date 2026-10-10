import argparse
import time
from uuid import uuid4

import httpx


def request(client, method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    assert response.status_code < 400, (method, path, response.status_code, response.text)
    return response.json()


def login(client, role):
    email = f"smoke-{uuid4().hex}@example.org"
    request(
        client,
        "POST",
        "/auth/register",
        json={
            "email": email,
            "password": "local-smoke-password",
            "roles": [role],
            "first_name": "Иван",
            "last_name": "Демонстрационный",
            "accept_processing": True,
        },
    )
    value = request(
        client, "POST", "/auth/login", json={"email": email, "password": "local-smoke-password"}
    )
    client.headers["Authorization"] = "Bearer " + value["access_token"]
    return value["user_id"]


def operation(client, value):
    for _ in range(90):
        value = request(client, "GET", f"/operations/{value['id']}")
        if value["state"] == "succeeded":
            return value
        assert value["state"] not in ("failed", "uncertain"), value
        time.sleep(1)
    raise AssertionError("Сервисная операция не завершилась за время проверки")


def main():
    parser = argparse.ArgumentParser(
        description="Сквозная HTTP-проверка на отдельном демонстрационном стенде"
    )
    parser.add_argument("--url", required=True, help="Адрес отдельного API")
    parser.add_argument(
        "--test",
        action="store_true",
        help="Дополнительно пройти настоящий taskgen и проверить сохранение неподтверждённой категории при провале",
    )
    args = parser.parse_args()
    with (
        httpx.Client(base_url=args.url, timeout=120) as candidate,
        httpx.Client(base_url=args.url, timeout=120) as employer,
    ):
        request(candidate, "GET", "/health")
        candidate_id = login(candidate, "candidate")
        categories = request(candidate, "GET", "/references/categories")
        specializations = request(candidate, "GET", "/references/specializations")
        backend = next(item for item in specializations if item["code"] == "backend")
        grades = request(candidate, "GET", "/references/grades")
        middle = next(
            item
            for item in grades
            if item["code"] == "middle" and item["industry_id"] == backend["industry_id"]
        )
        category = next(
            item
            for item in categories
            if item["specialization_id"] == backend["id"] and item["grade_id"] == middle["id"]
        )
        survey = request(candidate, "GET", "/surveys")[0]
        details = request(candidate, "GET", f"/surveys/{survey['id']}")
        answers = []
        for question in details["questions"]:
            options = question["options"]
            selected = next(
                (
                    item
                    for item in options
                    if item.get("specialization_id") == backend["id"] or item["label"] == "Middle"
                ),
                options[0],
            )
            answers.append({"question_id": question["id"], "option_ids": [selected["id"]]})
        completed_survey = request(
            candidate,
            "POST",
            f"/surveys/{survey['id']}/submit",
            json={
                "specialization_id": backend["id"],
                "claimed_grade_id": middle["id"],
                "answers": answers,
            },
        )
        if args.test:
            started_test = request(
                candidate,
                "POST",
                "/tests",
                headers={"Idempotency-Key": uuid4().hex},
                json={"survey_session_id": completed_survey["id"], "skill_ids": []},
            )
            test_id = operation(candidate, started_test)["resource_id"]
            for _ in range(600):
                test = request(candidate, "GET", f"/tests/{test_id}")
                if test["service_state"] == "ready":
                    break
                assert test["status"] not in ("cancelled", "expired"), test
                time.sleep(1)
            assert test["service_state"] == "ready", test
            for task in test["tasks"]:
                if task["kind"] == "quiz":
                    choice = task["public"]["options"][0]["id"]
                    request(
                        candidate,
                        "POST",
                        f"/tests/{test_id}/tasks/{task['id']}/answer",
                        json={"choice": choice},
                    )
            finished_test = request(candidate, "POST", f"/tests/{test_id}/finish")
            operation(candidate, finished_test)
            scored = request(candidate, "GET", f"/tests/{test_id}/result")
            assert scored["outcome"] == "failed", scored
            own_profile = request(candidate, "GET", "/me/profile")
            assert (
                own_profile["grade_confirmed"] is False
                and own_profile["category"]["id"] == category["id"]
            )
            print(
                "Настоящий taskgen: задания с кодом проверены песочницей, ответы приняты, провал не изменил выбранную категорию"
            )
        request(
            candidate, "POST", "/me/consents", json={"kind": "profile_publication", "granted": True}
        )
        request(
            candidate, "PUT", "/me/privacy", json={"is_searchable": True, "show_contacts": True}
        )
        login(employer, "employer")
        request(
            employer,
            "POST",
            "/companies",
            json={"name": "Компания HTTP-проверки", "full_name": "Рекрутер"},
        )
        need = request(
            employer,
            "POST",
            "/needs",
            json={
                "title": "Backend-разработчик",
                "description": "Серверная разработка Python и PostgreSQL",
                "category_id": category["id"],
                "specialization_id": backend["id"],
                "grade_id": middle["id"],
                "salary_min": 100000,
                "salary_max": 200000,
            },
        )
        started = request(
            employer, "POST", f"/needs/{need['id']}/runs", headers={"Idempotency-Key": uuid4().hex}
        )
        run_id = operation(employer, started)["remote_id"]
        for _ in range(90):
            run = request(employer, "GET", f"/runs/{run_id}")
            if run["status"] == "done":
                break
            assert run["status"] != "failed", run
            time.sleep(1)
        assert run["status"] == "done", run
        results = request(employer, "GET", f"/runs/{run_id}/results")
        assert candidate_id in {item["candidate_id"] for item in results["results"]}, results
        assert "contacts" not in request(employer, "GET", f"/candidates/{candidate_id}")
        invitation = request(
            employer,
            "POST",
            "/invitations",
            json={
                "candidate_id": candidate_id,
                "need_id": need["id"],
                "message": "Приглашаем обсудить работу",
                "salary_min": 100000,
                "salary_max": 200000,
                "contact_method": "email",
                "contact_value": "hr@example.org",
            },
        )
        request(candidate, "POST", f"/invitations/{invitation['id']}/accept")
        assert "contacts" in request(employer, "GET", f"/candidates/{candidate_id}")
        pdf = employer.get(f"/candidates/{candidate_id}/profile.pdf")
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
        print(
            "Регистрация, опрос, категория, реальный matcher, приглашение, раскрытие контактов и PDF: успешно"
        )


if __name__ == "__main__":
    main()
