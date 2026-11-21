"""cffi build script for the ``evmlr._evmlr_cffi`` extension (API mode).

The extension is a thin C module compiled against ``evmlr_api.h`` and linked
with ``libevmlr.so``. It is built when the package is installed (``uv sync``,
``pip install``), or by hand with ``python src/evmlr/_build.py``.

Where the library comes from:

``EVMLR_INCLUDE_DIR``
    Directory containing ``evmlr_api.h``. Default: the ``evmlr-shuffle``
    submodule at the root of this repository.
``EVMLR_LIB_DIR``
    Directory containing ``libevmlr.so``. Default: the same submodule, after
    ``make -C evmlr-shuffle shared``.
``EVMLR_RPATH``
    ``1`` (default) embeds ``EVMLR_LIB_DIR`` as the extension's run path, so a
    development checkout works without ``LD_LIBRARY_PATH``. Set it to ``0``
    when the library is installed system-wide (e.g. ``/usr/local/lib`` in the
    Docker images).
"""

from __future__ import annotations

import os
from pathlib import Path

from cffi import FFI

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SUBMODULE = _REPO_ROOT / "evmlr-shuffle"

INCLUDE_DIR = Path(os.environ.get("EVMLR_INCLUDE_DIR", _SUBMODULE))
LIB_DIR = Path(os.environ.get("EVMLR_LIB_DIR", _SUBMODULE))
USE_RPATH = os.environ.get("EVMLR_RPATH", "1") != "0"

# Declarations mirrored from evmlr_api.h. In API mode cffi checks them against
# the real header when it compiles the extension, so enum values or signatures
# that drift from the C library fail the build instead of misbehaving at run time.
CDEF = """
#define EVMLR_ELECTION_ID_BYTES 32

typedef enum {
    EVMLR_OK = 0,
    EVMLR_ERR_TRUNCATED = 1,
    EVMLR_ERR_MAGIC = 2,
    EVMLR_ERR_VERSION = 3,
    EVMLR_ERR_TYPE = 4,
    EVMLR_ERR_PARAMS = 5,
    EVMLR_ERR_SHAPE = 6,
    EVMLR_ERR_VALUE = 7,
    EVMLR_ERR_TRAILING = 8,
    EVMLR_ERR_NOMEM = 9,
    EVMLR_ERR_ARG = 10,
    EVMLR_ERR_KEY = 11,
    EVMLR_ERR_INVALID = 12,
    EVMLR_ERR_TOO_FEW = 13,
    EVMLR_ERR_SPACE = 14
} evmlr_status;

typedef struct {
    uint8_t *data;
    size_t len;
} evmlr_bytes;

void evmlr_bytes_free(evmlr_bytes *b);
const char *evmlr_strerror(int status);

uint32_t evmlr_modulus(void);
uint32_t evmlr_vote_len(void);

int evmlr_keygen(evmlr_bytes *pk, evmlr_bytes *keypair);

int evmlr_params(evmlr_bytes *pp, const uint8_t *election_id, uint32_t k,
                 const uint8_t *const *pks, const size_t *pk_lens);
int evmlr_params_info(const uint8_t *pp, size_t pp_len, uint32_t *k, uint8_t *election_id);

int evmlr_cast(evmlr_bytes *ballot, const uint8_t *pp, size_t pp_len, int64_t voter_id,
               const uint16_t *vote, size_t vote_len);
int evmlr_check_ballot(const uint8_t *pp, size_t pp_len, const uint8_t *ballot, size_t ballot_len,
                       int64_t *voter_id);

int evmlr_first_batch(evmlr_bytes *batch, const uint8_t *pp, size_t pp_len,
                      const uint8_t *const *ballots, const size_t *ballot_lens, size_t n);
int evmlr_mix(evmlr_bytes *out_batch, evmlr_bytes *mix, const uint8_t *pp, size_t pp_len,
              const uint8_t *keypair, size_t keypair_len, const uint8_t *in_batch, size_t in_len,
              const uint8_t *prev_mix, size_t prev_len);
int evmlr_mix_info(const uint8_t *pp, size_t pp_len, const uint8_t *mix, size_t mix_len,
                   uint32_t *server, uint32_t *n_in, uint32_t *n_out, uint32_t *n_rejected,
                   uint32_t *rejected, uint8_t *reasons, size_t cap);

int evmlr_transcript(evmlr_bytes *transcript, const uint8_t *pp, size_t pp_len,
                     const uint8_t *const *mixes, const size_t *mix_lens, size_t k);
int evmlr_verify(uint16_t *votes, size_t cap, size_t *n_votes, const uint8_t *pp, size_t pp_len,
                 const uint8_t *const *ballots, const size_t *ballot_lens, size_t n,
                 const uint8_t *transcript, size_t transcript_len);
int evmlr_results(uint16_t *votes, size_t cap, size_t *n_votes, const uint8_t *pp, size_t pp_len,
                  const uint8_t *batch, size_t batch_len);
"""


def _make_builder() -> FFI:
    header = INCLUDE_DIR / "evmlr_api.h"
    if not header.is_file():
        raise RuntimeError(
            f"evmlr_api.h not found in {INCLUDE_DIR}. Initialise the submodule "  # noqa: S608 - not SQL
            "(git submodule update --init) or set EVMLR_INCLUDE_DIR."
        )
    if not any(LIB_DIR.glob("libevmlr.so*")):
        raise RuntimeError(
            f"libevmlr.so not found in {LIB_DIR}. Build it with "
            "`make -C evmlr-shuffle shared` or set EVMLR_LIB_DIR."
        )

    builder = FFI()
    builder.cdef(CDEF)
    extra_link_args = [f"-Wl,-rpath,{LIB_DIR}"] if USE_RPATH else []
    builder.set_source(
        "evmlr._evmlr_cffi",
        '#include "evmlr_api.h"',
        include_dirs=[str(INCLUDE_DIR)],
        library_dirs=[str(LIB_DIR)],
        libraries=["evmlr"],
        extra_link_args=extra_link_args,
    )
    return builder


ffibuilder = _make_builder()

if __name__ == "__main__":
    # Builds the extension next to this file, for working without an install.
    ffibuilder.compile(tmpdir=str(Path(__file__).resolve().parents[1]), verbose=True)
