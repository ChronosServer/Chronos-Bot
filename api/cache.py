"""Response cache + ETag handling for route handlers.

Contract (checked at decoration time; violations raise TypeError):
  * handler is a plain `def` (run in a threadpool) or an `async def`;
  * it declares parameters named `request` (Request) and `conn`
    (conn=Depends(db.get_db)); data_version is read on that same conn
    before the handler runs;
  * it returns JSON-able data, or a Response (non-200 is returned uncached).

The cache key (and the ETag digest) uses only query params the handler
declares (by name or Query alias), so cache-busters like `?_=123` share one
entry. The LRU is bounded by total body bytes (MAX_BYTES) and entry count
(MAX_ENTRIES); bodies over MAX_BODY are served but never cached.

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
MAX_BYTES = 128 * 1024 * 1024
MAX_BODY = 8 * 1024 * 1024
CACHE_CONTROL = "public, max-age=60, stale-while-revalidate=900"
_lru: "OrderedDict[tuple, bytes]" = OrderedDict()
_bytes = 0


def clear():
    global _bytes
    _lru.clear()
    _bytes = 0


def _store(key, body):
    global _bytes
    if len(body) > MAX_BODY:
        return
    old = _lru.pop(key, None)
    if old is not None:
        _bytes -= len(old)
    _lru[key] = body
    _bytes += len(body)
    while _lru and (_bytes > MAX_BYTES or len(_lru) > MAX_ENTRIES):
        _, dropped = _lru.popitem(last=False)
        _bytes -= len(dropped)


def _query_names(params):
    """Query-string names a handler accepts: each parameter's name, or its
    Query(alias=...) when it has one. request/conn are never query params."""
    names = set()
    for name, p in params.items():
        if name in ("request", "conn"):
            continue
        alias = getattr(p.default, "alias", None)
        names.add(alias if isinstance(alias, str) and alias else name)
    return frozenset(names)


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
    accepted = _query_names(params)

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        request = kwargs["request"]
        version = db.data_version(kwargs["conn"])
        query = tuple(sorted((k, v) for k, v in request.query_params.multi_items()
                             if k in accepted))
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
            _store(key, body)
        else:
            _lru.move_to_end(key)
        return Response(content=body, media_type="application/json", headers=headers)
    return wrapper
