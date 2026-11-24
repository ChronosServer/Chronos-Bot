"""Read-only stats API."""
import orjson
from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from api.routes import leaderboard, meta, series


class ORJSONResponse(JSONResponse):
    # fastapi.responses.ORJSONResponse is deprecated; same behaviour here.
    def render(self, content) -> bytes:
        return orjson.dumps(content)


def create_app() -> FastAPI:
    app = FastAPI(title="Chronos Stats", default_response_class=ORJSONResponse)
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    @app.exception_handler(ValueError)
    async def _value_error(request: Request, exc: ValueError):
        return ORJSONResponse({"error": str(exc)}, status_code=400)

    app.include_router(meta.router, prefix="/api/stats")
    app.include_router(leaderboard.router, prefix="/api/stats")
    app.include_router(series.router, prefix="/api/stats")
    return app
