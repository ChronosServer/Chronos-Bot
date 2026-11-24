"""Response cache + ETag handling for route handlers.

Contract (checked at decoration time; violations raise TypeError):
  * handler is a plain `def` (run in a threadpool) or an `async def`;
  * it declares parameters named `request` (Request) and `conn`
    (conn=Depends(db.get_db)); data_version is read on that same conn
    before the handler runs;
  * it returns JSON-able data, or a Response (non-200 is returned uncached).

Usage:
    @router.get("/thing")
    @cached
    def thing(request: Request, conn=Depends(db.get_db)): return {...}
"""
import functools
import hashlib
import inspect
from collections import OrderedDict

import orjson
from fastapi import Response
from starlette.concurrency import run_in_threadpool

from api import db

MAX_ENTRIES = 2048
CACHE_CONTROL = "public, max-age=60, stale-while-revalidate=900"
_lru: "OrderedDict[tuple, bytes]" = OrderedDict()


def clear():
    _lru.clear()


def _matches(header, etag):
    if not header:
        return False
    tags = [t.strip() for t in header.split(",")]
    return "*" in tags or any(t.removeprefix("W/") == etag for t in tags)


def cached(fn):
    params = inspect.signature(fn).parameters
    missing = [n for n in ("request", "conn") if n not in params]
    if missing:
        raise TypeError(
            f"@cached handler {fn.__name__!r} must declare parameter(s) "
            f"{missing}: def {fn.__name__}(request: Request, conn=Depends(db.get_db), ...)")
    is_async = inspect.iscoroutinefunction(fn)

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        request = kwargs["request"]
        version = db.data_version(kwargs["conn"])
        query = tuple(sorted(request.query_params.multi_items()))
        path = request.url.path
        key = (path, query, version)
        qs = "&".join(f"{k}={v}" for k, v in query)
        digest = hashlib.sha1(f"{path}?{qs}".encode()).hexdigest()[:12]
        etag = f'"{version}-{digest}"'
        headers = {"ETag": etag, "Cache-Control": CACHE_CONTROL}
        if _matches(request.headers.get("if-none-match"), etag):
            return Response(status_code=304, headers=headers)
        body = _lru.get(key)
        if body is None:
            if is_async:
                result = await fn(*args, **kwargs)
            else:
                result = await run_in_threadpool(fn, *args, **kwargs)
            if isinstance(result, Response):
                if result.status_code != 200:
                    return result
                body = result.body
            else:
                body = orjson.dumps(result, option=orjson.OPT_NON_STR_KEYS)
            _lru[key] = body
            if len(_lru) > MAX_ENTRIES:
                _lru.popitem(last=False)
        else:
            _lru.move_to_end(key)
        return Response(content=body, media_type="application/json", headers=headers)
    return wrapper
