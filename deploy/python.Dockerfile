# One image per Python service: build with --build-arg PACKAGE=ballot-box or
# PACKAGE=mixnet-node, from the repository root as context:
#   docker build -f deploy/python.Dockerfile --build-arg PACKAGE=ballot-box .
#
# Ubuntu 26.04 for FLINT >= 3.2, as in evmlr-shuffle's CI (24.04 ships 3.0.1).
FROM ubuntu:26.04

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential libflint-dev libgmp-dev python3 python3-dev ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /src

# The C library first: it changes less often than the Python code.
# ARCH= builds without -march=native, so the image runs on any x86-64 host,
# not only on CPUs like the build machine's.
COPY evmlr-shuffle evmlr-shuffle
RUN make -C evmlr-shuffle shared ARCH= -j"$(nproc)"

ARG PACKAGE
COPY pyproject.toml uv.lock ./
COPY packages packages
RUN test -n "$PACKAGE" && uv sync --frozen --no-dev --package "$PACKAGE"

ENV PATH=/src/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1
RUN useradd --system --home /data voting && mkdir -p /data && chown voting /data
USER voting
WORKDIR /data
