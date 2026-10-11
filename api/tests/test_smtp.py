import os
import re
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from test_api import account


async def received_token(recipient, subject):
    url = os.environ["API_TEST_MAILPIT_URL"].rstrip("/")
    async with httpx.AsyncClient(base_url=url) as mailbox:
        listing = (await mailbox.get("/api/v1/messages")).json()
        for item in listing["messages"]:
            if item["Subject"] != subject or recipient not in {
                person["Address"] for person in item["To"]
            }:
                continue
            detail = (await mailbox.get(f"/api/v1/message/{item['ID']}")).json()
            match = re.search(r"https?://\S+", detail["Text"])
            assert match is not None
            return parse_qs(urlsplit(match.group()).query)["token"][0]
    raise AssertionError("SMTP-сервер не получил ожидаемое письмо")


async def test_real_smtp_verification_and_password_reset(client):
    if not os.environ.get("API_TEST_MAILPIT_URL"):
        pytest.skip(
            "Для проверки SMTP задайте API_TEST_MAILPIT_URL и API_TEST_SMTP_PORT локального Mailpit"
        )
    settings = client.app.state.settings
    settings.demo_auth_allow_unverified = False
    settings.demo_email_tokens = False
    settings.smtp_host = "127.0.0.1"
    settings.smtp_port = int(os.environ["API_TEST_SMTP_PORT"])
    settings.smtp_sender = "noreply@fsp.example"
    settings.smtp_starttls = False
    headers, _ = await account(client)
    identity = await client.get("/me", headers=headers)
    assert identity.status_code == 200, identity.text
    assert identity.json()["email_verified"] is False
    assert (await client.get("/me/profile", headers=headers)).status_code == 403
    recipient = identity.json()["email"]
    verification = await client.post("/auth/email/request", headers=headers)
    assert verification.status_code == 202, verification.text
    assert "token" not in verification.json()
    token = await received_token(recipient, "Подтверждение почты ФСП")
    assert (await client.post("/auth/email/verify", json={"token": token})).status_code == 200
    assert (await client.get("/me", headers=headers)).json()["email_verified"] is True
    assert (await client.get("/me/profile", headers=headers)).status_code == 200
    assert (await client.post("/auth/email/verify", json={"token": token})).status_code == 422
    assert (
        await client.post("/auth/password/request", json={"email": recipient})
    ).status_code == 202
    token = await received_token(recipient, "Восстановление пароля ФСП")
    reset = await client.post(
        "/auth/password/reset", json={"token": token, "password": "new-test-password-456"}
    )
    assert reset.status_code == 200, reset.text
    assert (await client.get("/me", headers=headers)).status_code == 401
    assert (
        await client.post("/auth/login", json={"email": recipient, "password": "test-password-123"})
    ).status_code == 401
    assert (
        await client.post(
            "/auth/login", json={"email": recipient, "password": "new-test-password-456"}
        )
    ).status_code == 200
    assert (
        await client.post(
            "/auth/password/reset", json={"token": token, "password": "other-test-password-456"}
        )
    ).status_code == 422
