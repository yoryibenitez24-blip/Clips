"""Serves files from local storage (development). With S3 the URLs are presigned instead."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.services.storage import LocalStorage, content_type_for, get_storage

router = APIRouter(prefix="/api/media", tags=["media"])


@router.get("/{key:path}")
def get_media(key: str):
    storage = get_storage()
    if not isinstance(storage, LocalStorage):
        raise HTTPException(404, "Media is served from object storage on this deployment")
    try:
        path = storage.local_path(key)
    except ValueError as exc:
        raise HTTPException(400, "Invalid media key") from exc
    if not path.is_file():
        raise HTTPException(404, "File not found")
    headers = {"Cache-Control": "private, max-age=3600"}
    if path.suffix == ".zip":
        headers["Content-Disposition"] = f'attachment; filename="{path.name}"'
    return FileResponse(path, media_type=content_type_for(path), headers=headers)
