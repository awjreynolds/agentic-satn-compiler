.PHONY: audit build dev format hooks hooks-install lint test test-fast typecheck

dev:
	uv sync --all-groups

lint:
	uv run ruff check .
	uv run ty check src/

format:
	uv run ruff check --fix .
	uv run ruff format src/ tests/ scripts/

typecheck:
	uv run ty check src/

test:
	uv run pytest --cov=satn --cov=lcwip --cov-report=term-missing --cov-fail-under=80

test-fast:
	uv run pytest --no-cov -x -q

audit:
	uv run pip-audit

hooks:
	uv run prek run

hooks-install:
	uv run prek install

build:
	uv build --no-sources
