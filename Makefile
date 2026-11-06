.PHONY: setup api worker beat web test dev-redis

PY ?= .venv/bin/python

setup:
	python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
	cd frontend && npm install

api:
	cd backend && ../$(PY) -m uvicorn app.main:app --reload --port 8000

worker:
	cd backend && ../.venv/bin/celery -A app.workers.celery_app:celery_app worker -Q analysis,render -c 2 --loglevel=INFO

beat:
	cd backend && ../.venv/bin/celery -A app.workers.celery_app:celery_app beat --loglevel=INFO

web:
	cd frontend && npm run dev

test:
	cd backend && ../$(PY) -m pytest -q
	cd frontend && npx tsc --noEmit

dev-redis:
	redis-server --port 6379 --save "" --appendonly no
