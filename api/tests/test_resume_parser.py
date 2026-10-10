import json

from app.resume_parser import ResumeLLMSettings, propose_resume


class Replies:
    def __init__(self, supported):
        self.calls = []
        self.replies = [
            json.dumps({"fields": [{"name": "city", "value": "Москва"}], "skills": []}),
            json.dumps(
                {
                    "verdicts": [
                        {
                            "sentence": 1,
                            "supported": supported,
                            "reason": "Проверка источника",
                            "spans": [1],
                        }
                    ]
                }
            ),
        ]

    async def complete(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return self.replies.pop(0)


async def test_resume_proposal_needs_verified_evidence():
    client = Replies(True)
    value = await propose_resume(client, ResumeLLMSettings(), "Город кандидата: Москва", {})
    assert value["fields"] == {"city": "Москва"}
    assert value["verified"] is True
    assert len(client.calls) == 2


async def test_resume_checker_rejection_does_not_publish_draft():
    client = Replies(False)
    value = await propose_resume(client, ResumeLLMSettings(), "Город кандидата: Москва", {})
    assert value is None


def test_resume_settings_do_not_reintroduce_embeddings():
    assert "embedding" not in ResumeLLMSettings().roles()
