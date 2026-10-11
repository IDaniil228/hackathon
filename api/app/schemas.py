from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, create_model

from app.db.models.assess import TestSession
from app.db.models.backend import ServiceOperation
from app.db.models.hire import Application, Company, EmployerNeed, Invitation, Vacancy
from app.db.models.matching import SearchRun


def entity_schema(model, name, exclude=(), extra=None):
    fields = {}
    for column in model.__table__.columns:
        if column.name in exclude:
            continue
        field_type = column.type.python_type
        if field_type is Decimal:
            field_type = float
        fields[column.name] = (field_type | None, None) if column.nullable else (field_type, ...)
    fields.update(extra or {})
    return create_model(name, **fields)


offer_fields = {
    "salary_basis": (Literal["gross"], "gross"),
    "salary_period": (Literal["month"], "month"),
}
CompanyOut = entity_schema(Company, "CompanyOut", extra={"full_name": (str | None, None)})
NeedOut = entity_schema(EmployerNeed, "NeedOut", extra={**offer_fields, "skills": (list[dict], [])})
VacancyOut = entity_schema(
    Vacancy, "VacancyOut", ["search_vector"], {**offer_fields, "skills": (list[dict], [])}
)
InvitationOut = entity_schema(
    Invitation,
    "InvitationOut",
    extra={**offer_fields, "company_name": (str | None, None), "events": (list[dict], [])},
)
ApplicationOut = entity_schema(Application, "ApplicationOut")
OperationOut = entity_schema(
    ServiceOperation, "OperationOut", ["payload", "response", "fingerprint", "key"]
)
RunOut = entity_schema(SearchRun, "RunOut", ["summary", "error_message", "params"])


class TokenOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"]
    user_id: UUID | None = None
    email_verified: bool | None = None


class RegistrationOut(BaseModel):
    id: UUID
    status: str
    email_verified: bool


class CandidateCard(BaseModel):
    user_id: UUID
    demo: bool = False
    category: dict | None = None
    grade_confirmed: bool = False
    profile_strength: float = 0
    first_name: str | None = None
    last_name: str | None = None
    middle_name: str | None = None
    city: str | None = None
    relocation: bool | None = None
    work_format: str | None = None
    experience_months: int | None = None
    about: str | None = None
    contacts: dict[str, str | None] | None = None
    links: list[dict] = []
    skills: list[dict] = []
    preferences: dict | None = None
    fsp_achievements: list[dict] = []
    profile: dict | None = None
    privacy: dict | None = None


class CandidateList(BaseModel):
    total: int
    results: list[CandidateCard]


class MatchResult(BaseModel):
    candidate_id: UUID
    rank: int
    final_score: float | None
    candidate: CandidateCard
    explanation: str
    factors: dict[str, dict[str, float | bool]] = {}


class MatchResults(BaseModel):
    total: int
    matched: int
    results: list[MatchResult]
    summary: str


class MapCoordinate(BaseModel):
    x: float
    y: float


class MapPoint(MapCoordinate):
    candidate_id: UUID
    cosine: float
    cluster: int
    rank: int | None = None
    exploration: Literal["fresh", "random"] | None = None
    candidate: CandidateCard


class MapOut(BaseModel):
    state: Literal["ready", "unavailable", "stale"]
    phase: Literal["preview", "final"] | None = None
    algorithm: Literal["pca"] | None = None
    query: MapCoordinate | None = None
    points: list[MapPoint] = []


class PublicTask(BaseModel):
    id: UUID
    position: int | None = None
    kind: str | None = None
    status: str
    statement: str | None = None
    public: dict | None = None
    failure: str | None = None


TestOut = entity_schema(
    TestSession,
    "TestOut",
    extra={
        "service_state": (str, ...),
        "failure": (str | None, None),
        "tasks_issued": (bool, False),
        "tasks": (list[PublicTask], []),
        "result_applied": (bool, False),
    },
)


class CompletedTest(BaseModel):
    status: Literal["done"]
    result: dict


class TestResultOut(BaseModel):
    result: dict
    outcome: str | None
    assigned_category_id: int | None
    applied_at: datetime | None
    service_state: str
