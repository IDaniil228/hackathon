import asyncio
from datetime import timedelta
from uuid import UUID

from sqlalchemy import text, update
from test_api import account

from app.db.enums import TokenPurpose
from app.db.models.core import EmailToken, User
from app.security import hash_token, new_token, now


async def test_reset_does_not_lock_token_before_account(client):
    _, user_id = await account(client)
    token = new_token()
    async with client.app.state.sessionmaker() as session:
        session.add(
            EmailToken(
                user_id=UUID(user_id),
                purpose=TokenPurpose.RESET_PASSWORD,
                token_hash=hash_token(token),
                expires_at=now() + timedelta(hours=1),
            )
        )
        await session.commit()
    async with client.app.state.sessionmaker() as first:
        await first.get(User, UUID(user_id), with_for_update=True)
        pid = await first.scalar(text("SELECT pg_backend_pid()"))
        resetting = asyncio.create_task(
            client.post(
                "/auth/password/reset", json={"token": token, "password": "concurrent-password-123"}
            )
        )
        try:
            blocked = False
            for _ in range(200):
                async with client.app.state.sessionmaker() as observer:
                    blocked = bool(
                        await observer.scalar(
                            text(
                                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE pid != :pid AND wait_event_type = 'Lock' AND :pid = ANY(pg_blocking_pids(pid)))"
                            ),
                            {"pid": pid},
                        )
                    )
                if blocked:
                    break
                await asyncio.sleep(0.01)
            assert blocked, "Второй запрос не дошёл до блокировки аккаунта"
            await first.execute(
                update(EmailToken)
                .where(
                    EmailToken.user_id == UUID(user_id),
                    EmailToken.purpose == TokenPurpose.RESET_PASSWORD,
                )
                .values(used_at=now())
            )
            await first.commit()
            response = await asyncio.wait_for(resetting, 5)
            assert response.status_code == 422, response.text
        finally:
            await first.rollback()
            if not resetting.done():
                resetting.cancel()
            await asyncio.gather(resetting, return_exceptions=True)
