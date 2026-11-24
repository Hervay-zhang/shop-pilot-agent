.PHONY: install run test lint check smoke compose-up compose-down logs

install:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements-dev.txt

run:
	.venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	.venv/bin/pytest -q

lint:
	.venv/bin/ruff check app tests scripts

check: lint test

smoke:
	.venv/bin/python scripts/smoke_test.py

compose-up:
	docker compose up -d --build

compose-down:
	docker compose down

logs:
	docker compose logs -f --tail=200
