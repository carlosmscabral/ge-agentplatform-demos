FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

COPY pyproject.toml ./
COPY app ./app

RUN pip install --upgrade pip && pip install .

EXPOSE 8080
CMD ["python", "-m", "app.main"]
