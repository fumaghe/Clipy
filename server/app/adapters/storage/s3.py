"""Storage S3-compatibile (Cloudflare R2). L'URL è quello pubblico stabile (public_base_url), niente presigned."""
from __future__ import annotations

from pathlib import Path

import httpx

from app.adapters.storage.base import StoredAsset
from app.core.errors import PermanentError, TemporaryError


class S3Storage:
    name = "s3"

    def __init__(self, *, bucket: str, endpoint_url: str, region: str, access_key: str, secret_key: str,
                 public_base_url: str, key_prefix: str = "clipy/", client=None):
        if not (bucket and public_base_url):
            raise PermanentError("Storage S3: bucket e public_base_url sono obbligatori", code="storage_not_configured")
        if client is None:
            import boto3

            client = boto3.client("s3", endpoint_url=endpoint_url or None, region_name=region or "auto",
                                  aws_access_key_id=access_key, aws_secret_access_key=secret_key)
        self.client = client
        self.bucket = bucket
        self.base = public_base_url.rstrip("/")
        self.prefix = key_prefix

    def upload(self, path: Path, key: str) -> StoredAsset:
        full = f"{self.prefix}{key}"
        try:
            self.client.upload_file(str(path), self.bucket, full, ExtraArgs={"ContentType": "video/mp4"})
        except Exception as exc:
            raise TemporaryError(f"Upload storage fallito: {exc}", code="storage_upload_failed") from exc
        return StoredAsset(key=full, url=f"{self.base}/{full}", public=True)

    def verify_public(self, url: str) -> tuple[bool, str]:
        """HEAD senza cookie/auth: deve rispondere 200 con content-type video."""
        try:
            with httpx.Client(timeout=20, follow_redirects=True) as c:
                r = c.head(url)
                if r.status_code in (403, 405):
                    r = c.get(url, headers={"Range": "bytes=0-1023"})
        except httpx.HTTPError as exc:
            return False, f"URL non raggiungibile: {exc}"
        ctype = r.headers.get("content-type", "")
        if r.status_code not in (200, 206):
            return False, f"HTTP {r.status_code}"
        if not ctype.startswith("video/"):
            return False, f"Content-Type inatteso: {ctype}"
        return True, "ok"

    def delete(self, key: str) -> None:
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            raise TemporaryError(f"Cancellazione asset fallita: {exc}", code="storage_delete_failed") from exc
