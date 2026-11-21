# mixnet-voting-server

A Helios-like internet voting system built on
[evmlr-shuffle](evmlr-shuffle/), a lattice-based verifiable mixnet. Voters
encrypt and prove their ballots in the browser (WebAssembly). The ballot box
handles authentication, elections, voter registration, casting and the
bulletin board. Independent mixnet nodes decrypt, shuffle and prove in turn,
and anyone can verify the count from public data.

> **Status:** scaffold. evmlr-shuffle is a research prototype. Its README
> lists open issues, notably that the per-layer proofs of encryption still
> let anyone link ballots to votes, and that correct decryption is not
> proven yet. Do not run real elections on it.

## Layout

```
evmlr-shuffle/          submodule: the protocol in C, native and WebAssembly builds
packages/
  evmlr-py/             Python bindings (cffi, API mode) over evmlr_api.h
  common/               voting_common: code shared by both servers (blob store, ...)
  ballot-box/           main server: FastAPI app + Celery worker
  mixnet-node/          one mixing server: FastAPI app holding one secret key
frontend/               Vue 3 + TypeScript + Vite: booth, board, verifier, admin
deploy/                 Dockerfile and a development compose stack
```

## Development setup

Requirements: GCC, FLINT >= 3.2 and GMP (e.g. `pacman -S flint` or
`apt install libflint-dev` on Ubuntu 26.04), [uv](https://docs.astral.sh/uv/),
Node.js, and [emsdk](https://emscripten.org/) for the WebAssembly build.

```sh
git submodule update --init
make sync            # builds evmlr-shuffle/libevmlr.so, then `uv sync`
make test            # every Python test suite
make dev-ballot-box  # http://localhost:8000/docs
make wasm            # once, for the frontend: WASM deps + module
make frontend        # http://localhost:5173
```

The Python environment is a [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/):
one `.venv` at the root, with every package installed in editable mode.
`evmlr-py`'s extension links `libevmlr.so` from the submodule, so after
changing the C code run `make lib` and reinstall the bindings with
`uv sync --reinstall-package evmlr`.

Docker: `cp deploy/.env.example deploy/.env`, fill in the key, then
`docker compose -f deploy/compose.yaml up --build`.
