.PHONY: backend frontend dev seed up down test build eval eval-mock eval-fault-injection

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

# RAG 质量门（真实 API，消耗 token）。任一指标跌破 eval/gate.py 阈值 → 退出码 1
eval:
	cd backend && uv run python ../eval/retrieval.py
	cd backend && uv run python ../eval/e2e.py
	cd backend && uv run python ../eval/features.py
	cd backend && uv run python ../eval/sweep.py

# 离线自检：确定性替身，零 token，验证评测脚手架与门禁逻辑
eval-mock:
	cd backend && uv run python ../eval/e2e.py --mock --db ../eval/eval_mock.db

# 故障注入回归：断言「判分器失效 → 门禁红灯」（**预期退出码为 1**）
eval-fault-injection:
	cd backend && uv run python ../eval/e2e.py --mock --mock-judge-garbage --db ../eval/eval_mock.db

build:
	cd frontend && npm run build
