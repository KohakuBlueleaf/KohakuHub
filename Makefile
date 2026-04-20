SHELL := /bin/bash
PYTHON ?= $(if $(wildcard ./venv/bin/python),./venv/bin/python,python)
TEST_ROOT ?= test/kohakuhub
SOURCE_ROOT ?= src/kohakuhub
RANGE_DIR ?=
TEST_RANGE = $(if $(strip $(RANGE_DIR)),$(TEST_ROOT)/$(RANGE_DIR),$(TEST_ROOT))
COV_RANGE = $(if $(strip $(RANGE_DIR)),$(SOURCE_ROOT)/$(RANGE_DIR),$(SOURCE_ROOT))
COV_FAIL_UNDER ?= $(if $(strip $(RANGE_DIR)),0,50)
COV_TYPES ?= xml term-missing
PYTEST_ARGS ?= -ra -vv --durations=10 --cov=$(COV_RANGE) --cov-config=.coveragerc --cov-fail-under=$(COV_FAIL_UNDER) $(shell for type in $(COV_TYPES); do echo --cov-report=$$type; done)

.PHONY: help init-env install-backend install-frontend install infra-up infra-down \
	backend seed-demo reset-local-data reset-and-seed ui admin status \
	logs-postgres logs-minio logs-lakefs test

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
	@echo "  make test             Run the full backend pytest suite against the real test services with coverage"
	@echo "                        Example: make test RANGE_DIR=api"
	@echo "                        Example: make test RANGE_DIR=api/repo/routers"
	@echo "                        Options: COV_TYPES='xml term-missing'"
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

test:
	@if [[ ! -e "$(TEST_RANGE)" ]]; then \
		echo "Missing test range: $(TEST_RANGE)" >&2; \
		exit 1; \
	fi
	@if [[ ! -e "$(COV_RANGE)" ]]; then \
		echo "Missing coverage range: $(COV_RANGE)" >&2; \
		exit 1; \
	fi
	$(PYTHON) -m pytest $(TEST_RANGE) $(PYTEST_ARGS)

status:
	docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | grep 'kohakuhub-dev-' || true

logs-postgres:
	docker logs -f kohakuhub-dev-postgres

logs-minio:
	docker logs -f kohakuhub-dev-minio

logs-lakefs:
	docker logs -f kohakuhub-dev-lakefs
