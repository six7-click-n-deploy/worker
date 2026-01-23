# Worker Development Makefile

.PHONY: help install lint format test test-unit test-integration test-cov clean

help: ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Available targets:'
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install dependencies
	poetry install --with dev

lint: ## Run all linters
	poetry run ruff check .
	poetry run black --check .
	poetry run isort --check-only .
	poetry run mypy app/ --ignore-missing-imports

format: ## Auto-format code
	poetry run black .
	poetry run isort .
	poetry run ruff check --fix .

test: ## Run all tests
	poetry run pytest -v

test-unit: ## Run only unit tests
	poetry run pytest -v -m "unit or not integration"

test-integration: ## Run only integration tests
	poetry run pytest -v -m integration

test-cov: ## Run tests with coverage report
	poetry run pytest -v --cov=app --cov-report=html --cov-report=term
	@echo "Coverage report generated in htmlcov/index.html"

test-watch: ## Run tests in watch mode
	poetry run pytest-watch

clean: ## Clean up generated files
	rm -rf .pytest_cache
	rm -rf .mypy_cache
	rm -rf .ruff_cache
	rm -rf htmlcov
	rm -rf .coverage
	rm -rf coverage.xml
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

check: lint test ## Run linters and tests (CI simulation)
	@echo "✅ All checks passed!"

docker-build: ## Build Docker image
	docker build -t worker:dev -f Dockerfile.dev .

docker-run: ## Run Docker container
	docker run --rm -it worker:dev
