.PHONY: backend frontend dev seed up down test build

# 本地开发（无 Docker，SQLite + Milvus Lite）
backend:
	cd backend && uv run uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

seed:
	cd backend && uv run python -m app.seed

dev:
	@echo "分别开两个终端运行："
	@echo "  make backend"
	@echo "  make frontend"

# Docker 全栈（MySQL + Milvus Standalone + Redis + 前后端）
up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f backend

test:
	cd backend && uv run pytest tests/unit -q

build:
	cd frontend && npm run build
