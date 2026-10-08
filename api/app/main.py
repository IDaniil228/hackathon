"""Точка входа FastAPI."""

from fastapi import FastAPI

from app.handlers import routers

app = FastAPI(
    title="Платформа ФСП",
    description="Агрегатор ИТ-вакансий с верифицированным профилем участника ФСП",
    version="0.1.0",
)

for router in routers:
    app.include_router(router)
