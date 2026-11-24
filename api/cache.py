"""Response cache + ETag handling for route handlers.

Usage:
    @router.get("/thing")
    @cached
    async def thing(request: Request, conn=Depends(get_db)): return {...}
"""
import functools
import hashlib
from collections import OrderedDict

import orjson
from fastapi import Response

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
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        request = kwargs.get("request") or next(a for a in args if hasattr(a, "url"))
        conn = db.get_conn()
        try:
            version = db.data_version(conn)
        finally:
            conn.close()
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
            result = await fn(*args, **kwargs)
            body = result.body if isinstance(result, Response) else orjson.dumps(result)
            _lru[key] = body
            if len(_lru) > MAX_ENTRIES:
                _lru.popitem(last=False)
        else:
            _lru.move_to_end(key)
        return Response(content=body, media_type="application/json", headers=headers)
    return wrapper
