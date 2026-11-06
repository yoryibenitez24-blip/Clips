"""DB-backed cache for expensive analysis keyed by source fingerprint."""
from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import AnalysisCache


def fingerprint_file(path: Path, probe_duration: float | None = None) -> str:
    h = hashlib.sha256()
    size = path.stat().st_size
    h.update(str(size).encode())
    if probe_duration is not None:
        h.update(f"{probe_duration:.2f}".encode())
    chunk = 4 * 1024 * 1024
    with path.open("rb") as fh:
        h.update(fh.read(chunk))
        if size > 2 * chunk:
            fh.seek(size - chunk)
            h.update(fh.read(chunk))
    return h.hexdigest()[:40]


def cache_get(db: Session, key: str) -> dict | None:
    row = db.get(AnalysisCache, key)
    return row.data if row else None


def cache_set(db: Session, key: str, kind: str, data: dict) -> None:
    row = db.get(AnalysisCache, key)
    if row:
        row.data = data
    else:
        db.add(AnalysisCache(key=key, kind=kind, data=data))
    db.commit()
