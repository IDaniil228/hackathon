import argparse
from uuid import uuid4

import httpx
from live_smoke import login, request


def main():
    parser = argparse.ArgumentParser(
        description="Проверить переписку через запущенный API на отдельном стенде"
    )
    parser.add_argument("--url", required=True)
    args = parser.parse_args()
    with (
        httpx.Client(base_url=args.url, timeout=30) as candidate,
        httpx.Client(base_url=args.url, timeout=30) as employer,
    ):
        candidate_id = login(candidate, "candidate")
        request(
            candidate, "POST", "/me/consents", json={"kind": "profile_publication", "granted": True}
        )
        request(candidate, "PUT", "/me/privacy", json={"is_searchable": True})
        login(employer, "employer")
        request(
            employer,
            "POST",
            "/companies",
            json={"name": "Компания переписки", "full_name": "Рекрутер"},
        )
        offer = request(
            employer,
            "POST",
            "/invitations",
            json={
                "candidate_id": candidate_id,
                "message": "Предлагаем обсудить работу в команде",
                "salary_min": 150000,
                "salary_max": 200000,
                "contact_method": "platform",
            },
        )
        conversation = request(employer, "POST", f"/invitations/{offer['id']}/conversation")
        path = f"/conversations/{conversation['id']}"
        assert conversation["can_send"]
        request(
            employer,
            "POST",
            path + "/messages",
            json={"content": "Когда удобно поговорить?", "client_message_id": str(uuid4())},
        )
        request(
            candidate,
            "POST",
            path + "/messages",
            json={"content": "Завтра после обеда", "client_message_id": str(uuid4())},
        )
        history = request(candidate, "GET", path + "/messages")
        assert len(history["results"]) == 3 and history["results"][0]["kind"] == "offer"
        assert request(employer, "GET", f"/invitations/{offer['id']}")["status"] == "sent"
        assert "contacts" not in request(employer, "GET", f"/candidates/{candidate_id}")
        request(candidate, "POST", path + "/read", json={})
        assert request(candidate, "GET", path)["unread_count"] == 0
        request(candidate, "POST", path + "/close")
        assert (
            employer.post(
                path + "/messages",
                json={"content": "Ещё вопрос", "client_message_id": str(uuid4())},
            ).status_code
            == 409
        )
        print(
            "Переписка до принятия предложения, сохранение приватности, прочтение и закрытие: успешно"
        )


if __name__ == "__main__":
    main()
