# Development entry points. The C library must be built before the Python
# environment, because evmlr-py's cffi extension links against libevmlr.so.

.PHONY: help lib wasm sync test lint migrate dev-ballot-box dev-mixnet-node frontend clean

help:
	@echo "lib              build evmlr-shuffle/libevmlr.so (needs FLINT >= 3.2)"
	@echo "wasm             build the WebAssembly module for the frontend (needs emsdk)"
	@echo "sync             lib + uv sync (installs every Python package into .venv)"
	@echo "test             run the Python and frontend test suites"
	@echo "lint             ruff check"
	@echo "migrate          bring the ballot box's database up to date"
	@echo "dev-ballot-box   run the ballot box with auto-reload on :8000"
	@echo "dev-mixnet-node  run one mixnet node with auto-reload on :8100"
	@echo "frontend         install and run the frontend dev server"

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

migrate:
	uv run python -m ballot_box.db

dev-ballot-box:
	uv run uvicorn ballot_box.app:create_app --factory --reload --port 8000

dev-mixnet-node:
	uv run uvicorn mixnet_node.app:create_app --factory --reload --port 8100

frontend:
	cd frontend && npm install && npm run dev

clean:
	$(MAKE) -C evmlr-shuffle clean
	rm -rf .venv packages/evmlr-py/build packages/evmlr-py/src/evmlr/_evmlr_cffi*
