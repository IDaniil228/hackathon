from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.security import decode_access, encode_access, hash_token
from app.settings import Settings
from app.visibility import contact_access, safe_match_result


def test_token_bound_to_issuer_and_expiry():
    settings = Settings(jwt_secret="a" * 48)
    token = encode_access(uuid4(), uuid4(), settings)
    assert decode_access(token, settings)["iss"] == settings.jwt_issuer
    with pytest.raises(HTTPException):
        decode_access(token, Settings(jwt_secret="a" * 48, jwt_issuer="other"))
    expired = encode_access(
        uuid4(), uuid4(), Settings(jwt_secret="a" * 48, access_token_minutes=-1)
    )
    with pytest.raises(HTTPException):
        decode_access(expired, settings)


def test_contact_policy_requires_both_public_opt_ins():
    assert not contact_access(False, False, True, True)
    assert not contact_access(False, True, False, True)
    assert not contact_access(False, True, True, False)
    assert contact_access(False, True, True, True)
    assert contact_access(True, False, False, False)


def test_history_never_forwards_untrusted_text():
    row = {
        "candidate_id": str(uuid4()),
        "rank": 1,
        "final_score": 0.8,
        "explanation": "secret@example.org",
        "factors": {"line": "private"},
    }
    result = safe_match_result(row, {"user_id": row["candidate_id"], "grade_confirmed": True})
    assert result["candidate"]["grade_confirmed"] is True
    assert "secret" not in str(result)
    assert "private" not in str(result)


def test_refresh_hash_is_not_plaintext():
    assert len(hash_token("secret")) == 64
    assert hash_token("secret") != "secret"
