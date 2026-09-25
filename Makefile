.PHONY: help build-rust build-rust-dev setup-python run-core run-paper run-all test test-python test-rust lint format clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

build-rust: ## Build the Rust workspace (release)
	cargo build --release

build-rust-dev: ## Build the Rust workspace (debug)
	cargo build

setup-python: ## Setup Python virtual environment
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt

run-core: build-rust ## Start only the Rust ingestion engine
	cd rust/sentinel-ingestion && ../../target/release/sentinel-ingestion

run-paper: ## Start the Python pipeline in paper mode (it launches the Rust engine itself)
	mkdir -p data
	PYTHONPATH=python .venv/bin/python -m sentinel.main --mode paper

run-all: build-rust run-paper ## Build the Rust engine, then start the paper pipeline

test: test-python test-rust ## Run all tests

test-python: ## Run Python tests only
	.venv/bin/pytest tests/ -v

test-rust: ## Run Rust tests only
	cargo test --workspace

lint: ## Lint Python code
	.venv/bin/ruff check .
	.venv/bin/black --check .

format: ## Format Python code
	.venv/bin/black .
	.venv/bin/ruff check --fix .

clean: ## Clean build artifacts
	rm -rf __pycache__ .pytest_cache
	find . -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
	cargo clean
