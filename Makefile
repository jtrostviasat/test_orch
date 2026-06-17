# Convenience targets. `make up` is the one-shot bring-up.
.PHONY: up down logs build test lint fmt ps psql rabbit

up:            ## Build + start the full stack (detached)
	docker compose up --build -d

down:          ## Stop and remove containers
	docker compose down

logs:          ## Tail all service logs
	docker compose logs -f

build:         ## Build images without starting
	docker compose build

ps:            ## Show service status
	docker compose ps

test:          ## Run the unit test suite locally
	pytest

lint:          ## Lint with ruff
	ruff check .

fmt:           ## Auto-format with ruff
	ruff format .

psql:          ## Open a psql shell on the postgres service
	docker compose exec postgres psql -U test_orch -d test_orch

rabbit:        ## Print the RabbitMQ management UI URL
	@echo "RabbitMQ UI: http://localhost:15672 (guest/guest)"