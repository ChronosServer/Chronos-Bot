"""Read-only stats API."""
from contextlib import asynccontextmanager

import orjson
from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from api import db
from api.routes import feeds, leaderboard, meta, player, search, series


class ORJSONResponse(JSONResponse):
    # fastapi.responses.ORJSONResponse is deprecated; same behaviour here.
    def render(self, content) -> bytes:
        return orjson.dumps(content)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    db.check_required_indexes()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Chronos Stats", default_response_class=ORJSONResponse,
                  lifespan=_lifespan)
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    @app.exception_handler(ValueError)
    async def _value_error(request: Request, exc: ValueError):
        return ORJSONResponse({"error": str(exc)}, status_code=400)

    app.include_router(meta.router, prefix="/api/stats")
    app.include_router(leaderboard.router, prefix="/api/stats")
    app.include_router(series.router, prefix="/api/stats")
    app.include_router(player.router, prefix="/api/stats")
    app.include_router(search.router, prefix="/api/stats")
    app.include_router(feeds.router, prefix="/api/stats")
    return app
