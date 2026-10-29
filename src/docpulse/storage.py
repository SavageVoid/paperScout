"""Object-store abstraction.

Pipeline code talks to ``ObjectStore`` only. ``S3Store`` works against any S3-compatible
endpoint (SeaweedFS locally, AWS S3 in the cloud: same code, different endpoint).
``LocalStore`` is a filesystem implementation for tests and for running without Docker.
"""

from __future__ import annotations

import os
import tempfile
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Any

from docpulse.config import StorageSettings


class StorageError(RuntimeError):
    pass


class ObjectNotFound(KeyError):  # noqa: N818  (mirrors the stdlib's FileNotFoundError naming)
    pass


def validate_key(key: str) -> None:
    parts = key.split("/")
    if not key or key.startswith("/") or "\\" in key or any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"invalid object key: {key!r}")


def require_dir_prefix(prefix: str) -> None:
    """Deleting by prefix is dangerous: ``ingest_date=2026-10-2`` would also match
    ``ingest_date=2026-10-27``. Require a trailing slash so a prefix is a whole 'directory'."""
    if not prefix or not prefix.endswith("/") or prefix.startswith("/") or ".." in prefix.split("/"):
        raise ValueError(f"delete_prefix needs a non-root prefix ending in '/': {prefix!r}")


class ObjectStore(ABC):
    @abstractmethod
    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...

    @abstractmethod
    def get_bytes(self, key: str) -> bytes: ...

    @abstractmethod
    def list_keys(self, prefix: str) -> list[str]: ...

    @abstractmethod
    def delete_prefix(self, prefix: str) -> int: ...


class LocalStore(ObjectStore):
    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        validate_key(key)
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise ValueError(f"key escapes store root: {key!r}")
        return path

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)  # atomic on POSIX
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def get_bytes(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError as exc:
            raise ObjectNotFound(key) from exc

    def list_keys(self, prefix: str) -> list[str]:
        keys = []
        for path in self.root.rglob("*"):
            if path.is_file() and not path.name.startswith(".tmp-"):
                key = path.relative_to(self.root).as_posix()
                if key.startswith(prefix):
                    keys.append(key)
        return sorted(keys)

    def delete_prefix(self, prefix: str) -> int:
        require_dir_prefix(prefix)
        keys = self.list_keys(prefix)
        for key in keys:
            self._path(key).unlink()
        # prune now-empty directories under the prefix (bottom-up)
        base = self.root / prefix
        if base.exists():
            for d in sorted((p for p in base.rglob("*") if p.is_dir()), reverse=True):
                if not any(d.iterdir()):
                    d.rmdir()
            if not any(base.iterdir()):
                base.rmdir()
        return len(keys)


def _error_code(exc: Exception) -> str | None:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return str(response.get("Error", {}).get("Code"))
    return None


class S3Store(ObjectStore):
    def __init__(
        self,
        bucket: str,
        client: Any,
        *,
        region: str = "us-east-1",
        custom_endpoint: bool = False,
        delete_batch: int = 1000,
    ) -> None:
        self.bucket = bucket
        self._client = client
        self._region = region
        self._custom_endpoint = custom_endpoint
        self._delete_batch = delete_batch  # S3 DeleteObjects accepts at most 1000 keys

    @classmethod
    def from_settings(cls, settings: StorageSettings) -> S3Store:
        import boto3  # imported lazily so unit tests need no AWS SDK
        from botocore.config import Config

        base: dict[str, Any] = {
            "signature_version": "s3v4",
            "s3": {"addressing_style": "path"},  # local hostnames can't resolve bucket subdomains
            "retries": {"max_attempts": 5, "mode": "standard"},
        }
        try:
            # boto3 >= 1.36 adds default checksum headers that several S3-compatible servers
            # reject; only send them when the API requires them.
            config = Config(
                **base,
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            )
        except TypeError:  # older botocore does not know these options
            config = Config(**base)

        client = boto3.client(
            "s3",
            endpoint_url=settings.endpoint_url,
            region_name=settings.region,
            aws_access_key_id=settings.access_key,
            aws_secret_access_key=settings.secret_key,
            config=config,
        )
        return cls(
            settings.bucket,
            client,
            region=settings.region,
            custom_endpoint=settings.endpoint_url is not None,
        )

    def ensure_bucket(
        self, attempts: int = 10, delay_s: float = 3.0, sleep: Callable[[float], None] = time.sleep
    ) -> bool:
        """Create the bucket if missing. Retries because the store may still be starting.
        Returns True if the bucket was created."""
        last: Exception | None = None
        for i in range(attempts):
            try:
                self._client.head_bucket(Bucket=self.bucket)
                return False
            except Exception as exc:  # noqa: BLE001 - botocore error types vary by failure
                if _error_code(exc) in ("404", "NoSuchBucket", "NotFound"):
                    kwargs: dict[str, Any] = {"Bucket": self.bucket}
                    if not self._custom_endpoint and self._region != "us-east-1":
                        kwargs["CreateBucketConfiguration"] = {"LocationConstraint": self._region}
                    self._client.create_bucket(**kwargs)
                    return True
                last = exc
                if i < attempts - 1:
                    sleep(delay_s)
        raise StorageError(f"object store not reachable for bucket {self.bucket!r}") from last

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        validate_key(key)
        self._client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def get_bytes(self, key: str) -> bytes:
        validate_key(key)
        try:
            return self._client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except Exception as exc:  # noqa: BLE001
            if _error_code(exc) in ("NoSuchKey", "404"):
                raise ObjectNotFound(key) from exc
            raise

    def list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys.extend(obj["Key"] for obj in page.get("Contents", []))
        return sorted(keys)

    def delete_prefix(self, prefix: str) -> int:
        require_dir_prefix(prefix)
        keys = self.list_keys(prefix)
        for i in range(0, len(keys), self._delete_batch):
            batch = keys[i : i + self._delete_batch]
            resp = self._client.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
            )
            if resp.get("Errors"):
                raise StorageError(f"failed to delete some objects: {resp['Errors'][:3]}")
        return len(keys)
