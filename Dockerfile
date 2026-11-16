# Mock help-desk and metrics API (SYNTHETIC data) for the docker compose stack.
# The n8n workflows' HTTP tools and HTTP Request nodes call it at http://mock-services:8080.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/src
WORKDIR /app
RUN pip install --no-cache-dir "starlette>=0.40" "uvicorn>=0.29"
COPY src/n8nkit/__init__.py src/n8nkit/mocks.py ./src/n8nkit/

RUN useradd --create-home app
USER app
EXPOSE 8080
HEALTHCHECK --interval=5s --retries=10 CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"
CMD ["uvicorn", "n8nkit.mocks:app", "--host", "0.0.0.0", "--port", "8080"]
