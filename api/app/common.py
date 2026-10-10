from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.inspection import inspect


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def record(obj, exclude=()):
    return {
        column.key: getattr(obj, column.key)
        for column in inspect(obj).mapper.column_attrs
        if column.key not in exclude
    }


async def found(session, model, key, lock=False):
    obj = await session.get(model, key, with_for_update=lock)
    if obj is None:
        raise HTTPException(404, "Запись не найдена")
    return obj


def assign(obj, values):
    for key, value in values.items():
        setattr(obj, key, value)


def own_company(obj, employer):
    if obj.company_id != employer.company_id:
        raise HTTPException(404, "Запись не найдена")
