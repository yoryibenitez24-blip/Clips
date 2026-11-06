from __future__ import annotations

from fastapi import APIRouter

from app.services.options import options_payload

router = APIRouter(prefix="/api", tags=["options"])


@router.get("/options")
def options() -> dict:
    return options_payload()


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}
