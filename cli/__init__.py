"""External CLI plugins distributed alongside AEGIS.

The CLI shells out to the public `/api/v1` surface so it can be packaged
as a standalone script (or eventually a separate PyPI package) without
needing to import the FastAPI app or the ORM. That separation keeps the
CI footprint small — only `httpx` is required to run an audit gate.
"""
