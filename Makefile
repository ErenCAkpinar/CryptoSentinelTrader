.PHONY: help build-rust setup-python run-paper run-core test clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

build-rust: ## Build Rust core engine (release)
	cd core_engine && cargo build --release

build-rust-dev: ## Build Rust core engine (debug)
	cd core_engine && cargo build

setup-python: ## Setup Python virtual environment
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt

run-core: build-rust ## Start Rust core engine
	./core_engine/target/release/sentinel-core

run-paper: ## Start Python pipeline in paper trading mode
	PYTHONPATH=python .venv/bin/python -m sentinel.main --mode paper

run-all: ## Start both core engine and pipeline
	@echo "Starting Rust core engine in background..."
	./core_engine/target/release/sentinel-core &
	@sleep 2
	@echo "Starting Python pipeline..."
	PYTHONPATH=python .venv/bin/python -m sentinel.main --mode paper

test: ## Run all tests
	.venv/bin/pytest tests/ -v
	cd core_engine && cargo test

test-python: ## Run Python tests only
	.venv/bin/pytest tests/ -v

test-rust: ## Run Rust tests only
	cd core_engine && cargo test

lint: ## Lint Python code
	.venv/bin/ruff check .
	.venv/bin/black --check .

format: ## Format Python code
	.venv/bin/black .
	.venv/bin/ruff check --fix .

clean: ## Clean build artifacts
	rm -rf __pycache__ .pytest_cache
	find . -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
	cd core_engine && cargo clean
