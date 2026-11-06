"""Object storage abstraction: local filesystem (dev) or any S3-compatible bucket."""
from __future__ import annotations

import shutil
from pathlib import Path

from app.config import Settings, get_settings


class Storage:
    def put_file(self, local_path: Path, key: str, content_type: str | None = None) -> str: ...

    def get_file(self, key: str, dest: Path) -> Path: ...

    def url_for(self, key: str) -> str: ...

    def delete(self, key: str) -> None: ...

    def exists(self, key: str) -> bool: ...

    def delete_prefix(self, prefix: str) -> None: ...


class LocalStorage(Storage):
    def __init__(self, root: Path, api_base_url: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.api_base_url = api_base_url.rstrip("/")

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root.resolve() not in p.parents and p != self.root.resolve():
            raise ValueError("invalid storage key")
        return p

    def put_file(self, local_path: Path, key: str, content_type: str | None = None) -> str:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local_path, dest)
        return key

    def get_file(self, key: str, dest: Path) -> Path:
        shutil.copyfile(self._path(key), dest)
        return dest

    def url_for(self, key: str) -> str:
        return f"{self.api_base_url}/api/media/{key}"

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.exists():
            p.unlink()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def delete_prefix(self, prefix: str) -> None:
        p = self._path(prefix)
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)

    def local_path(self, key: str) -> Path:
        return self._path(key)


class S3Storage(Storage):
    def __init__(self, settings: Settings):
        import boto3

        self.bucket = settings.s3_bucket
        self.public_base = settings.s3_public_base_url.rstrip("/") if settings.s3_public_base_url else None
        self.expiry = settings.s3_presign_expiry_seconds
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
        )

    def put_file(self, local_path: Path, key: str, content_type: str | None = None) -> str:
        extra = {"ContentType": content_type} if content_type else {}
        self.client.upload_file(str(local_path), self.bucket, key, ExtraArgs=extra or None)
        return key

    def get_file(self, key: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, key, str(dest))
        return dest

    def url_for(self, key: str) -> str:
        if self.public_base:
            return f"{self.public_base}/{key}"
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=self.expiry
        )

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    def delete_prefix(self, prefix: str) -> None:
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            objs = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if objs:
                self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": objs})


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        s = get_settings()
        if s.storage_backend == "s3":
            _storage = S3Storage(s)
        else:
            _storage = LocalStorage(s.local_storage_dir, s.api_base_url)
    return _storage


def content_type_for(path: Path | str) -> str:
    suffix = Path(path).suffix.lower()
    return {".mp4": "video/mp4", ".zip": "application/zip", ".jpg": "image/jpeg", ".png": "image/png",
            ".json": "application/json", ".ass": "text/plain"}.get(suffix, "application/octet-stream")
