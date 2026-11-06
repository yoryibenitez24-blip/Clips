from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import clips, jobs, media, options, projects, videos
from app.config import get_settings
from app.db import init_db
from app.services.errors import ClipsError

settings = get_settings()
logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Clips API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=False,
                   allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(ClipsError)
async def clips_error_handler(_request: Request, exc: ClipsError) -> JSONResponse:
    return JSONResponse(status_code=exc.http_status, content={"detail": {"code": exc.code, "message": exc.message}})


for router in (videos.router, jobs.router, projects.router, clips.router, media.router, options.router):
    app.include_router(router)
