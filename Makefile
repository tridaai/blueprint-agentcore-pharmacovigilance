# Trida AI Blueprint — Pharmacovigilance Adverse Event Case Agent
# on AWS Bedrock AgentCore
#
# Primary workflow is uv (https://docs.astral.sh/uv/):
#   make install     uv sync --extra dev
#   make demo        planted reports end to end, offline (no keys)
#   make test        pytest
#   make evals       eval gate, offline (SQLite stack)
#   make serve       the AgentCore Runtime app on http://localhost:8080
#   make api         the case API on http://localhost:8000
#   make invoke      local in-process invoke of the agent step
#
# No uv? python3 -m venv .venv && source .venv/bin/activate, then:
#   make install-pip

.PHONY: install install-pip demo test evals serve api invoke reports-check model-ledger-check signals-check steward-check console-check

install:
	uv sync --extra dev

install-pip:
	# The frozen set first, then the package itself with --no-deps: pip
	# refuses an editable install under a hashed constraints file, so the
	# two steps never mix.
	pip install -r constraints.txt
	pip install --no-deps -e .
	pip install "pytest==9.1.1" "httpx==0.28.1"

demo:
	uv run pv-agent-demo

test:
	uv run pytest -q

evals:
	uv run python evals/run_evals.py

reports-check:
	uv run python scripts/check_reports.py

model-ledger-check:
	uv run python scripts/check_model_ledger.py

signals-check:
	uv run python scripts/check_signals.py

steward-check:
	uv run python scripts/check_steward.py

console-check:
	uv run python scripts/check_console.py

serve:
	uv run pv-agent-serve

api:
	uv run uvicorn pv_cases.api:app --port 8000

invoke:
	uv run pv-agent-invoke
