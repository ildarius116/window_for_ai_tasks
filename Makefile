.PHONY: up down ps logs build gen-secrets gen-ssl setup prod backup restore deploy-functions

gen-ssl:
	@bash scripts/gen-selfsigned-cert.sh

up: gen-ssl
	docker compose up -d

down:
	docker compose down

build:
	docker compose build

ps:
	docker compose ps

logs:
	docker compose logs -f

logs-openwebui:
	docker compose logs -f openwebui

logs-litellm:
	docker compose logs -f litellm

gen-secrets:
	@echo "LITELLM_MASTER_KEY=$$(openssl rand -hex 32)"
	@echo "OPENWEBUI_SECRET_KEY=$$(openssl rand -hex 32)"
	@echo "POSTGRES_PASSWORD=$$(openssl rand -hex 16)"
	@echo "LANGFUSE_NEXTAUTH_SECRET=$$(openssl rand -hex 32)"
	@echo "LANGFUSE_SALT=$$(openssl rand -hex 32)"

reset:
	docker compose down -v
	docker compose up -d --build

setup: up
	@echo "Waiting for services to start..."
	@sleep 10
	@echo "Run scripts/setup-models.sh after creating admin account in OpenWebUI"
	@echo "Open http://localhost:3000 to create admin account"

prod: gen-ssl
	docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

backup:
	bash scripts/backup.sh

restore:
	@if [ -z "$(DB)" ] || [ -z "$(FILE)" ]; then \
		echo "Usage: make restore DB=<database> FILE=<backup_file>"; \
		echo "Example: make restore DB=memory FILE=backups/memory_2026-03-29_120000.sql.gz"; \
		exit 1; \
	fi
	bash scripts/restore.sh $(DB) $(FILE)

deploy-functions:
	@if [ -z "$$OWUI_ADMIN_TOKEN" ]; then \
		echo "❌ OWUI_ADMIN_TOKEN not set. Get it from OpenWebUI → Profile → API Keys, then: export OWUI_ADMIN_TOKEN=<token>"; \
		exit 1; \
	fi
	@bash scripts/deploy_function.sh pipelines/auto_router_function.py
	@bash scripts/deploy_function.sh pipelines/memory_function.py
	@echo "✅ Functions deployed. Enable them in OpenWebUI → Admin → Functions."
