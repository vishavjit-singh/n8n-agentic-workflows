PY ?= python3
export PYTHONPATH := src

.PHONY: install demo validate render simulate docs test lint serve docker n8n-import down clean

install:
	$(PY) -m pip install -r requirements-dev.txt

## lint the exports → draw the graphs → run the SYNTHETIC scenarios → charts → docs/workflows/*.md
demo: clean
	$(PY) -m n8nkit demo

validate:
	$(PY) -m n8nkit validate --strict

render:
	$(PY) -m n8nkit render

## one run in the simulator, e.g. make simulate WF=01-lead-intake-qualifier PAYLOAD=examples/lead.json
WF ?= 01-lead-intake-qualifier
PAYLOAD ?= examples/lead.json
simulate:
	$(PY) -m n8nkit simulate $(WF) --payload $(PAYLOAD)

docs:
	$(PY) -m n8nkit docs

test:
	$(PY) -m pytest -q

lint:
	$(PY) -m ruff check .

## the mock help-desk / metrics API on http://127.0.0.1:8080
serve:
	$(PY) -m n8nkit serve-mocks

.env:
	cp .env.example .env
	@echo "Created .env from .env.example: set N8N_ENCRYPTION_KEY and POSTGRES_PASSWORD before going further."

## n8n + postgres + one-shot workflow import + mock API (needs Docker)
docker: .env
	docker compose up -d --build
	@echo "n8n: http://localhost:5678   mock API: http://localhost:8080/health"

n8n-import: .env
	docker compose run --rm n8n-import

down:
	docker compose down

clean:
	rm -rf reports
