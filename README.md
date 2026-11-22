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
scripts/                e2e.sh (make e2e), smoke_test.py, browser_test.mjs
.github/workflows/      CI: lint and tests (SQLite, Postgres), frontend, WASM + end to end
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
make migrate         # the ballot box's database (BALLOT_BOX_DATABASE_URL)
make dev-ballot-box  # http://localhost:8000/docs
make wasm            # once, for the frontend: WASM deps + module
make frontend        # http://localhost:5173
```

For the frontend in development, run the ballot box with
`BALLOT_BOX_PUBLIC_URL=http://localhost:5173`: the browser talks to Vite,
which proxies `/api` and `/auth` to the ballot box, and the ballot box checks
that requests come from its public URL. The booth and the verifier need the
WebAssembly module (`make wasm`).

A mixnet node refuses to start without its secrets. For development:

```sh
export MIXNET_NODE_API_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
export MIXNET_NODE_KEY_ENCRYPTION_KEY=$(python3 -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())")
make dev-mixnet-node  # http://localhost:8100/docs
```

The Python environment is a [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/):
one `.venv` at the root, with every package installed in editable mode.
`evmlr-py`'s extension links `libevmlr.so` from the submodule, so after
changing the C code run `make lib` and reinstall the bindings with
`uv sync --reinstall-package evmlr`.

The ballot box reads its default mix chain from `BALLOT_BOX_MIXNET_NODES`
(JSON: name, url and token of each node, in order). Ballot box tests use
SQLite; set `BALLOT_BOX_TEST_DATABASE_URL` to a scratch Postgres database to
run them there (it is wiped after each test).

## Logins and roles

People log in with a username and password (`password`) or through a CAS
server (`cas`), as enabled in `BALLOT_BOX_AUTH_PROVIDERS` (with
`BALLOT_BOX_CAS_SERVER_URL` for CAS). There is no self sign-up: an
administrator creates password logins. Voting needs no role: the voter roll
of each election lists who may vote, by provider and username.

Administrators may do everything; managers may create elections and manage
those they are a manager of. The first administrator comes from
`BALLOT_BOX_ADMIN_IDENTITIES` (e.g. `["cas:jdoe"]`, made admin on login) or
the command line:

```sh
uv run python -m ballot_box.accounts set-password admin --admin
uv run python -m ballot_box.accounts grant cas:jdoe manager
```

`BALLOT_BOX_ADMIN_TOKEN`, if set, is a bearer token with administrator
rights for scripts.

## Elections

An election has one or more questions, and each question is a separate
election for evmlr: its own election id (a hash of the election, the
question's position and its spec), its own key on every mix server, its own
mix chain and tally. The manager picks the mix servers and their order; the
default nodes are suggested. Answers are encoded into evmlr's vote
polynomials by versioned encodings (`n_of_m/v1` for now) and counted by
versioned methods; a decrypted vote that does not decode is left out of the
result and listed with the reason. The booth's TypeScript encoder and the
Python one are both checked against `test-vectors/encodings.json`
(regenerate with `python -m ballot_box.questions.vectors` when adding cases).

Voters use `/api/elections/{id}`: the public election, each question's
parameters and bulletin board, ballot trackers, `me` and casting (one
multipart request with a part per question, stored all or nothing). Results,
transcripts and mix records become public when the manager publishes them.

## End-to-end tests

`make e2e` starts a throwaway stack on this machine (three mixnet nodes, the
ballot box, a Celery worker, Redis in Docker, the Vite dev server) and runs
one election through it: `scripts/smoke_test.py` over HTTP, then, when the
WebAssembly module is built and Chromium is installed,
`scripts/browser_test.mjs` through the real pages in headless Chromium
(voting, the admin page, verification in the browser). It stops only what
it started. CI runs the same script.

If gmplib.org is unreachable for `make wasm`, put
`gmp-6.3.0.tar.xz` from a GNU mirror (`https://ftpmirror.gnu.org/gmp/`) in
`evmlr-shuffle/wasm/deps/src/` first; CI does this and checks its SHA-256.

## Docker

`cp deploy/.env.example deploy/.env` and fill in the values, build the
frontend (`make wasm frontend-build`), then
`docker compose -f deploy/compose.yaml up --build` and open
http://localhost:8080 (nginx: the built frontend, with `/api` and `/auth`
proxied to the ballot box). Create the first administrator with
`docker compose -f deploy/compose.yaml exec ballot-box python -m ballot_box.accounts set-password admin --admin`.
