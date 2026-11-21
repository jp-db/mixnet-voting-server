# evmlr-py

Python bindings for [evmlr-shuffle](../../evmlr-shuffle/), generated with
cffi in API mode over `evmlr_api.h`, the library's C ABI on byte strings.

```python
import hashlib
import evmlr

# Each mixing server, on its own machine
keys = [evmlr.keygen() for _ in range(3)]  # publish .public_key, keep .keypair

# Election authority: public keys in, public parameters out
election_id = hashlib.sha256(b"Board election 2026").digest()
pp = evmlr.params(election_id, [k.public_key for k in keys])

# Ballot box, for each ballot received from a browser
voter_id = evmlr.check_ballot(pp, ballot)  # raises EvmlrError if invalid
assert voter_id == authenticated_voters_alias  # the binding is the caller's job

# Counting: server j, in turn, with its own key only
batch, records = evmlr.first_batch(pp, board), []
for j, k in enumerate(keys):
    out = evmlr.mix(pp, k.keypair, batch, records[-1] if j else None)
    batch, records = out.batch, records + [out.record]

# Anyone
votes = evmlr.verify(pp, board, evmlr.transcript(pp, records))
candidates = [v[0] for v in votes]
```

## Design

- **Bytes only.** Every object crosses the boundary in the library's
  canonical encoding, which is the same on native and WASM builds. Python
  never holds C memory: outputs are copied into `bytes`, and C buffers are
  freed before each function returns.
- **Errors** raise `EvmlrError`, whose `.status` is an `evmlr.Status`
  (e.g. `Status.INVALID` for a ballot whose proof fails). Bad arguments
  that Python can check (lengths, ranges) raise `ValueError` before calling C.
- **Thread safety.** The C library keeps one global random stream, so every
  call takes a module-wide lock. cffi releases the GIL while C runs, so an
  asyncio loop keeps serving requests, but calls are serialised per process.
  Use processes (Celery workers, `ProcessPoolExecutor`) for parallelism.
- **Drift checks.** The `cdef` in `_build.py` mirrors `evmlr_api.h`. In API
  mode cffi compiles against the real header, so a changed signature or enum
  value breaks the build instead of corrupting memory at run time.

## Building

`libevmlr.so` must exist first (`make -C evmlr-shuffle shared`, or `make
lib` at the repository root). These variables control where the build looks:

| Variable | Default | Meaning |
| --- | --- | --- |
| `EVMLR_INCLUDE_DIR` | `<repo>/evmlr-shuffle` | where `evmlr_api.h` is |
| `EVMLR_LIB_DIR` | `<repo>/evmlr-shuffle` | where `libevmlr.so` is |
| `EVMLR_RPATH` | `1` | embed `EVMLR_LIB_DIR` as run path; `0` for a system-wide install |
