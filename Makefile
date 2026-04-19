SHELL := /bin/bash
PYTHON ?= $(if $(wildcard ./venv/bin/python),./venv/bin/python,python)

.PHONY: help init-env install-backend install-frontend install infra-up infra-down \
	backend seed-demo reset-local-data reset-and-seed ui admin status \
	logs-postgres logs-minio logs-lakefs test-backend-prepare \
	test-backend-restore test-backend-clean test-backend-fast test-backend-cov

help:
	@echo "Local development targets:"
	@echo "  make init-env         Copy .env.dev.example to .env.dev if missing"
	@echo "  make install-backend  Install Python backend deps into the local venv"
	@echo "  make install-frontend Install JS deps for both frontend apps"
	@echo "  make install          Run backend + frontend dependency installation"
	@echo "  make infra-up         Start local Postgres/MinIO/LakeFS with persisted data"
	@echo "  make infra-down       Stop local infra containers but keep persisted data"
	@echo "  make seed-demo        Run migrations + first-run demo seed without starting uvicorn"
	@echo "  make reset-local-data Dangerously delete all persisted local KohakuHub dev data"
	@echo "  make reset-and-seed   Reset persisted local data, then bootstrap fresh demo data"
	@echo "  make backend          Run FastAPI backend in reload mode"
	@echo "  make ui               Run the main Vite frontend on :5173"
	@echo "  make admin            Run the admin Vite frontend on :5174"
	@echo "  make test-backend-prepare Build the fast backend test baseline"
	@echo "  make test-backend-restore Restore the active fast backend test state"
	@echo "  make test-backend-clean Remove the fast backend test state"
	@echo "  make test-backend-fast Run the fast backend pytest suite"
	@echo "  make test-backend-cov Run the fast backend suite with coverage"
	@echo "  make status           Show local dev infra container status"
	@echo "  make logs-postgres    Tail Postgres logs"
	@echo "  make logs-minio       Tail MinIO logs"
	@echo "  make logs-lakefs      Tail LakeFS logs"

init-env:
	@if [[ -f .env.dev ]]; then \
		echo ".env.dev already exists"; \
	else \
		cp .env.dev.example .env.dev; \
		echo "Created .env.dev from .env.dev.example"; \
	fi

install-backend:
	# Reuse the repo-local venv when present so local dev stays isolated from system Python.
	@if [[ -x ./venv/bin/pip ]]; then \
		./venv/bin/pip install -e ".[dev]"; \
	else \
		pip install -e ".[dev]"; \
	fi

install-frontend:
	npm install --prefix src/kohaku-hub-ui
	npm install --prefix src/kohaku-hub-admin

install: install-backend install-frontend

infra-up: init-env
	./scripts/dev/up_infra.sh

infra-down:
	./scripts/dev/down_infra.sh

backend: init-env
	./scripts/dev/run_backend.sh

seed-demo: infra-up
	# Force the one-time local demo bootstrap even if auto-seed is disabled in .env.dev.
	KOHAKU_HUB_DEV_AUTO_SEED=true ./scripts/dev/run_backend.sh --prepare-only

reset-local-data:
	./scripts/dev/reset_local_data.sh

reset-and-seed: reset-local-data
	$(MAKE) seed-demo

ui:
	npm run dev --prefix src/kohaku-hub-ui

admin:
	npm run dev --prefix src/kohaku-hub-admin

test-backend-prepare:
	$(PYTHON) scripts/tests/backend_fast_state.py prepare

test-backend-restore:
	$(PYTHON) scripts/tests/backend_fast_state.py restore

test-backend-clean:
	$(PYTHON) scripts/tests/backend_fast_state.py clean

test-backend-fast:
	$(PYTHON) scripts/tests/backend_fast_state.py pytest -- test -q

test-backend-cov:
	$(PYTHON) scripts/tests/backend_fast_state.py pytest -- test -q --cov=kohakuhub --cov-config=.coveragerc --cov-fail-under=50 --cov-report=term-missing --cov-report=xml

status:
	docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | grep 'kohakuhub-dev-' || true

logs-postgres:
	docker logs -f kohakuhub-dev-postgres

logs-minio:
	docker logs -f kohakuhub-dev-minio

logs-lakefs:
	docker logs -f kohakuhub-dev-lakefs
