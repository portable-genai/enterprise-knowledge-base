# A2 Enterprise Knowledge Base : developer Makefile.
#
# The default test/lint targets run under the LOCAL profile : a WORKING offline stack
# (SQLite FTS5 + deterministic LLM) that needs NO Google Cloud SDK and no emulator. It
# both runs the pipeline end to end and backs the test suite. Override PROFILE=gcp for the
# managed stack, or PROFILE=onprem to exercise the fail-fast migration placeholders.

PYTHON      ?= python3
PYTHON      := $(if $(wildcard .venv/bin/python),.venv/bin/python,$(PYTHON))
PIP         ?= pip
PROFILE     ?= local
SRC         := src/enterprise_kb
TESTS       := tests
API_HOST    ?= 127.0.0.1
API_PORT    ?= 8082
UI_DIR      := ui
TF_DIR      := infra/terraform

export KB_PROFILE := $(PROFILE)

.DEFAULT_GOAL := help
.PHONY: help install install-gcp lock fmt lint test smoke eval demo demo-selftest portability-demo ui-check check check-api-bind run-api run-ui tf-validate tf-plan clean

help: ## Show this help.
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Install the package + dev tooling (NO GCP SDK : local/test profile).
	$(PIP) install -e ".[dev]"

install-gcp: ## Install with the managed-stack extra (google-adk, genai, AlloyDB, DLP, ...).
	$(PIP) install -e ".[gcp,dev]"

lock: ## Recompile universal dev/GCP locks and preserve immutable commons commit pins.
	$(PYTHON) scripts/lock.py

fmt: ## Auto-format and auto-fix lint issues.
	ruff format $(SRC) $(TESTS) eval
	ruff check --fix $(SRC) $(TESTS) eval

lint: ## Lint (ruff) and type-check (mypy).
	ruff check $(SRC) $(TESTS) eval scripts/kb_demo.py scripts/demo_selftest.py scripts/demo_server_selftest.py scripts/portability_demo.py scripts/rename_fork.py
	ruff format --check $(SRC) $(TESTS) eval scripts/kb_demo.py scripts/demo_selftest.py scripts/demo_server_selftest.py scripts/portability_demo.py scripts/rename_fork.py
	mypy $(SRC)

test: ## Run unit + contract tests on the local profile (no GCP SDK required).
	KB_PROFILE=local pytest -m 'not integration' -q

smoke: ## End-to-end offline smoke: answer a seeded query under the local profile.
	KB_PROFILE=local enterprise-knowledge-base answer \
		"What due diligence is required before onboarding a cloud provider?" \
		--principals "user:jane@bank.test"

eval: ## Run the A4-style eval gate (recall / ACL correctness / citation / safety).
	$(PYTHON) eval/run_eval.py

demo: ## Offline demo: run the governed-RAG flow and render static audit pages to ./out.
	KB_PROFILE=local PYTHONPATH=src $(PYTHON) scripts/kb_demo.py kb_demo.json
	KB_PROFILE=local PYTHONPATH=src $(PYTHON) scripts/render_kb_ui.py kb_demo.json ./out

demo-selftest: ## Run the real demo and assert domain, audit and rendered evidence.
	KB_PROFILE=local PYTHONPATH=src $(PYTHON) scripts/demo_selftest.py

portability: portability-demo ## Standard fleet alias for the executable portability proof.

portability-demo: ## Run the bounded executable portability proof.
	KB_PROFILE=local PYTHONPATH=src $(PYTHON) scripts/portability_demo.py

ui-check: ## Build, execute and audit the production UI artifact.
	cd $(UI_DIR) && npm ci && npm run lint && npm test && NEXT_TELEMETRY_DISABLED=1 npm run build && npm run assert-hydratable && npm audit --audit-level=high

plugin: ## Render the Agent Plugins 1.0.0 directory from this repo's own declarations.
	python scripts/render_plugin.py --dest dist/plugin

mcp-serve: ## Serve the governed tool catalog over MCP 2026-07-28 (stdio; needs [gcp]).
	python -m enterprise_kb.mcp

check: lint test eval demo-selftest portability-demo ui-check plugin ## Full offline quality gate.

check-api-bind:
	@case "$(API_HOST)" in \
		127.0.0.1|localhost|"::1") ;; \
		*) test "$${KB_ALLOW_INSECURE_DEMO:-}" = "1" || { \
			echo "Refusing non-loopback API_HOST=$(API_HOST); set KB_ALLOW_INSECURE_DEMO=1 to opt in." >&2; \
			exit 2; \
		} ;; \
	esac

run-api: check-api-bind ## Run the FastAPI service (PROFILE=$(PROFILE)); loopback unless explicitly opted in.
	KB_API_HOST="$(API_HOST)" KB_API_RELOAD=1 PORT="$(API_PORT)" $(PYTHON) -m enterprise_kb.api.app

run-ui: ## Run the React / Next.js UI (dev server).
	cd $(UI_DIR) && npm install && npm run dev

tf-validate: ## Offline posture check: terraform fmt + validate, NO cloud credentials.
	cd $(TF_DIR) && terraform fmt -check -recursive -diff \
		&& terraform init -backend=false -input=false \
		&& terraform validate -no-color

tf-plan: ## Terraform plan for the in-region infrastructure (needs credentials).
	cd $(TF_DIR) && terraform init -input=false && terraform plan

clean: ## Remove caches and build artefacts.
	rm -rf build dist *.egg-info .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
