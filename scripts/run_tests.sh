#!/usr/bin/env bash
set -euo pipefail
export APP_ENV=development
export DATABASE_URL=postgresql+asyncpg://test:test@localhost:5432/invoice_ai_test
export OPENAI_API_KEY=test_api_key
export SECRET_KEY=test-secret-key-minimum-32-characters-long

poetry run pytest tests/ -v --cov=app --cov-report=term-missing "$@"
