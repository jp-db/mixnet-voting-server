# The cffi extension cannot be declared in pyproject.toml alone; setuptools
# reads cffi_modules from here. Everything else lives in pyproject.toml.
from setuptools import setup

setup(cffi_modules=["src/evmlr/_build.py:ffibuilder"])
