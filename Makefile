.PHONY: setup migrate run test lint fmt check import geocode docker-up docker-logs docker-down

setup:            ## Install deps and create .env if missing
	uv sync
	@test -f .env || cp .env.example .env

migrate:
	uv run python manage.py migrate

run:
	uv run python manage.py runserver

test:
	uv run pytest

lint:
	uv run ruff check .

fmt:
	uv run ruff format .
	uv run ruff check --fix .

check: lint test   ## What CI runs

import:           ## Load fuel stations from data/ into the DB
	uv run python manage.py import_stations

geocode:          ## Resolve station coordinates (offline, one-time)
	uv run python manage.py geocode_stations

docker-up:        ## Build and start the container (see compose.yaml)
	docker compose up -d --build

docker-logs:
	docker compose logs -f --tail=100

docker-down:
	docker compose down
