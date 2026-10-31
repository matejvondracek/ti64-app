"""Pattern indexing of .up2 files (PyEBSDIndex; Intel Arc GPU via OpenCL optional, CPU fallback).
Reads patterns in chunks through ti64app.io.up2; never loads the whole file. (Stub.)"""
from __future__ import annotations


def index_up2(path, phases, chunk_size: int = 20000, **params):
    raise NotImplementedError("up2 indexing: not implemented yet")
