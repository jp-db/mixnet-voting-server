# Development entry points. The C library must be built before the Python
# environment, because evmlr-py's cffi extension links against libevmlr.so.

.PHONY: help lib wasm sync test lint e2e migrate dev-ballot-box dev-mixnet-node frontend frontend-build clean

help:
	@echo "lib              build evmlr-shuffle/libevmlr.so (needs FLINT >= 3.2)"
	@echo "wasm             build the WebAssembly module for the frontend (needs emsdk)"
	@echo "sync             lib + uv sync (installs every Python package into .venv)"
	@echo "test             run the Python and frontend test suites"
	@echo "lint             ruff check"
	@echo "e2e              smoke and browser tests against a throwaway local stack"
	@echo "migrate          bring the ballot box's database up to date"
	@echo "dev-ballot-box   run the ballot box with auto-reload on :8000"
	@echo "dev-mixnet-node  run one mixnet node with auto-reload on :8100"
	@echo "frontend         install and run the frontend dev server"
	@echo "frontend-build   build the frontend into frontend/dist (needs make wasm)"

lib:
	$(MAKE) -C evmlr-shuffle shared

wasm:
	cd evmlr-shuffle && { [ -d wasm/deps ] || wasm/build-deps.sh; } && $(MAKE) -f Makefile.wasm

sync: lib
	uv sync

test: sync
	uv run pytest
	cd frontend && npm test

lint:
	uv run ruff check packages scripts

e2e: sync
	scripts/e2e.sh

migrate:
	uv run python -m ballot_box.db

dev-ballot-box:
	uv run uvicorn ballot_box.app:create_app --factory --reload --port 8000

dev-mixnet-node:
	uv run uvicorn mixnet_node.app:create_app --factory --reload --port 8100

frontend:
	cd frontend && npm install && npm run dev

frontend-build:
	cd frontend && npm ci && npm run build

clean:
	$(MAKE) -C evmlr-shuffle clean
	rm -rf .venv packages/evmlr-py/build packages/evmlr-py/src/evmlr/_evmlr_cffi*
