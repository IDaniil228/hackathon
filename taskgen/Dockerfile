FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Необязательные группы зависимостей, например "--group nli" для essay_check_method=nli.
ARG UV_GROUPS=""

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev $UV_GROUPS --no-install-project

COPY . .
RUN uv sync --frozen --no-dev $UV_GROUPS

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && taskgen serve"]
