FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml ./
COPY app ./app

RUN uv sync --no-dev --frozen 2>/dev/null || uv sync --no-dev

ENV PYTHONUNBUFFERED=1
ENV PORT=8080

CMD ["uv", "run", "python", "-m", "app.main"]
