"""Provider-neutral immutable object storage for sealed forward evidence."""
from __future__ import annotations

import hashlib
import os
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


class ObjectStorageError(RuntimeError):
    pass


class ObjectIntegrityError(ObjectStorageError):
    pass


@dataclass(frozen=True)
class ObjectHead:
    key: str
    size: int
    checksum_sha256: str | None
    metadata: dict[str, str]
    modified_at: str | None = None


class ForwardObjectStorage(ABC):
    """Small interface shared by S3, R2, B2 and deterministic test doubles."""

    @abstractmethod
    def put_partition(self, key: str, source: Path, metadata: dict[str, str]) -> ObjectHead:
        raise NotImplementedError

    @abstractmethod
    def head_partition(self, key: str) -> ObjectHead | None:
        raise NotImplementedError

    def verify_partition(self, key: str, *, size: int, checksum_sha256: str) -> ObjectHead:
        head = self.head_partition(key)
        if head is None:
            raise ObjectStorageError(f"remote object is missing after upload: {key}")
        if head.size != int(size):
            raise ObjectIntegrityError(
                f"remote size mismatch for {key}: expected {size}, received {head.size}"
            )
        if head.checksum_sha256 != checksum_sha256:
            raise ObjectIntegrityError(
                f"remote checksum metadata mismatch for {key}: "
                f"expected {checksum_sha256}, received {head.checksum_sha256}"
            )
        return head

    @abstractmethod
    def get_partition(self, key: str, target: Path) -> Path:
        raise NotImplementedError

    @abstractmethod
    def delete_partition(self, key: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def list_partitions(self, prefix: str) -> Iterable[ObjectHead]:
        raise NotImplementedError


class S3CompatibleObjectStorage(ForwardObjectStorage):
    """S3 API adapter. boto3 is imported only inside the forward worker."""

    def __init__(self, *, client: Any, bucket: str):
        self.client = client
        self.bucket = bucket

    @classmethod
    def from_environment(cls) -> "S3CompatibleObjectStorage":
        required = {
            "bucket": os.getenv("FORWARD_OBJECT_BUCKET", "").strip(),
            "access_key": os.getenv("FORWARD_OBJECT_ACCESS_KEY_ID", "").strip(),
            "secret_key": os.getenv("FORWARD_OBJECT_SECRET_ACCESS_KEY", "").strip(),
        }
        missing = sorted(name for name, value in required.items() if not value)
        if missing:
            raise ObjectStorageError(
                "missing object-storage configuration: " + ", ".join(missing)
            )
        try:
            import boto3
        except ImportError as exc:
            raise ObjectStorageError("boto3 is required when forward object storage is enabled") from exc
        kwargs: dict[str, Any] = {
            "service_name": "s3",
            "aws_access_key_id": required["access_key"],
            "aws_secret_access_key": required["secret_key"],
        }
        optional = {
            "endpoint_url": os.getenv("FORWARD_OBJECT_ENDPOINT_URL", "").strip(),
            "region_name": os.getenv("FORWARD_OBJECT_REGION", "").strip(),
            "aws_session_token": os.getenv("FORWARD_OBJECT_SESSION_TOKEN", "").strip(),
        }
        kwargs.update({key: value for key, value in optional.items() if value})
        return cls(client=boto3.client(**kwargs), bucket=required["bucket"])

    @staticmethod
    def _head(key: str, response: dict[str, Any]) -> ObjectHead:
        metadata = {str(k).lower(): str(v) for k, v in (response.get("Metadata") or {}).items()}
        modified = response.get("LastModified")
        return ObjectHead(
            key=key, size=int(response.get("ContentLength") or 0),
            checksum_sha256=metadata.get("sha256"), metadata=metadata,
            modified_at=modified.isoformat() if hasattr(modified, "isoformat") else None,
        )

    def put_partition(self, key: str, source: Path, metadata: dict[str, str]) -> ObjectHead:
        existing = self.head_partition(key)
        expected_checksum = metadata["sha256"]
        if existing is not None:
            if existing.size == source.stat().st_size and existing.checksum_sha256 == expected_checksum:
                return existing
            raise ObjectIntegrityError(f"immutable object key collision with different content: {key}")
        with source.open("rb") as handle:
            self.client.put_object(
                Bucket=self.bucket, Key=key, Body=handle,
                ContentLength=source.stat().st_size,
                Metadata={str(k): str(v) for k, v in metadata.items()},
            )
        return self.verify_partition(
            key, size=source.stat().st_size, checksum_sha256=expected_checksum,
        )

    def head_partition(self, key: str) -> ObjectHead | None:
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise ObjectStorageError(f"object HEAD failed for {key}: {exc}") from exc
        return self._head(key, response)

    def get_partition(self, key: str, target: Path) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".download")
        try:
            with temporary.open("wb") as handle:
                self.client.download_fileobj(self.bucket, key, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            raise ObjectStorageError(f"object download failed for {key}: {exc}") from exc
        return target

    def delete_partition(self, key: str) -> None:
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            raise ObjectStorageError(f"object delete failed for {key}: {exc}") from exc

    def list_partitions(self, prefix: str) -> Iterable[ObjectHead]:
        token: str | None = None
        while True:
            args: dict[str, Any] = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                args["ContinuationToken"] = token
            try:
                response = self.client.list_objects_v2(**args)
            except Exception as exc:
                raise ObjectStorageError(f"object listing failed for {prefix}: {exc}") from exc
            for item in response.get("Contents") or ():
                head = self.head_partition(str(item["Key"]))
                if head is not None:
                    yield head
            if not response.get("IsTruncated"):
                return
            token = str(response["NextContinuationToken"])


class InMemoryObjectStorage(ForwardObjectStorage):
    """Byte-exact fake used by unit tests; supports deterministic fault injection."""

    def __init__(self):
        self.objects: dict[str, tuple[bytes, dict[str, str], str]] = {}
        self.available = True
        self.interrupt_next_upload = False
        self.put_attempts = 0

    def _available(self) -> None:
        if not self.available:
            raise ObjectStorageError("object storage unavailable")

    def put_partition(self, key: str, source: Path, metadata: dict[str, str]) -> ObjectHead:
        self._available()
        self.put_attempts += 1
        payload = source.read_bytes()
        existing = self.objects.get(key)
        if existing:
            if len(existing[0]) == len(payload) and existing[1].get("sha256") == metadata["sha256"]:
                return self.head_partition(key)  # type: ignore[return-value]
            raise ObjectIntegrityError(f"immutable object key collision with different content: {key}")
        if self.interrupt_next_upload:
            self.interrupt_next_upload = False
            raise ObjectStorageError("simulated interrupted upload")
        modified = datetime.now(timezone.utc).isoformat()
        self.objects[key] = (payload, dict(metadata), modified)
        return self.head_partition(key)  # type: ignore[return-value]

    def head_partition(self, key: str) -> ObjectHead | None:
        self._available()
        item = self.objects.get(key)
        if item is None:
            return None
        payload, metadata, modified = item
        return ObjectHead(key, len(payload), metadata.get("sha256"), dict(metadata), modified)

    def get_partition(self, key: str, target: Path) -> Path:
        self._available()
        if key not in self.objects:
            raise ObjectStorageError(f"remote object not found: {key}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.objects[key][0])
        return target

    def delete_partition(self, key: str) -> None:
        self._available()
        self.objects.pop(key, None)

    def list_partitions(self, prefix: str) -> Iterable[ObjectHead]:
        self._available()
        for key in sorted(item for item in self.objects if item.startswith(prefix)):
            head = self.head_partition(key)
            if head:
                yield head


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_verified(source: Path, target: Path, expected_sha256: str) -> Path:
    """Local helper for bounded replay caches."""
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    if sha256_file(target) != expected_sha256:
        target.unlink(missing_ok=True)
        raise ObjectIntegrityError(f"downloaded checksum mismatch: {target}")
    return target
