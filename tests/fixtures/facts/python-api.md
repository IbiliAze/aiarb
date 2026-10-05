# Repo facts

## Stack

- Languages: Python (8 files)
- Frameworks: FastAPI, SQLAlchemy
- Runtime versions: Python >=3.12 (pyproject.toml)

## Dependencies

- pyproject.toml (Python): runtime: fastapi, uvicorn, sqlalchemy, alembic, psycopg, redis; dev: pytest, httpx, ruff
- Lockfiles: uv.lock (uv)

## Tests

- Test files: 2, against 6 source files.
- Where they are: tests/ (2)
- Test tools: pytest

## Infrastructure

- Containers: Dockerfile, docker-compose.yml
- Base images: python:3.12-slim
- Migrations: alembic/ (2 files)
- Environment variables (.env.example): DATABASE_URL, REDIS_URL

## CI

- GitHub Actions, .github/workflows/ci.yml: CI, on push, pull_request
- .github/workflows/ci.yml job test: `uv sync --locked`; `uv run ruff check`; `uv run pytest`
