"""MinIO download/upload + checksum validation (master plan §27 steps
5-6, 14; ADR-004 — the bridge is where object references from a job
message actually turn into bytes).
"""
from __future__ import annotations

import hashlib
import pathlib

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

from noc_bridge.config import BridgeSettings


def get_client(settings: BridgeSettings):
    return boto3.client(
        "s3",
        endpoint_url=settings.minio_endpoint_url,
        aws_access_key_id=settings.minio_access_key,
        aws_secret_access_key=settings.minio_secret_key,
        config=BotoConfig(signature_version="s3v4"),
    )


def sha256_of_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ChecksumMismatch(RuntimeError):
    pass


def download_object(
    client, *, bucket: str, object_key: str, dest_path: pathlib.Path,
    expected_sha256: str | None, version_id: str | None = None
) -> None:
    """§27 step 5-6: download then validate checksum before the sandbox ever
    sees the file — a corrupted/tampered object must never reach the
    container."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    extra_args = {"VersionId": version_id} if version_id else None
    if extra_args:
        client.download_file(bucket, object_key, str(dest_path), ExtraArgs=extra_args)
    else:
        client.download_file(bucket, object_key, str(dest_path))

    if expected_sha256:
        actual = sha256_of_file(dest_path)
        if actual != expected_sha256:
            raise ChecksumMismatch(
                f"{bucket}/{object_key}: expected sha256 {expected_sha256}, got {actual}"
            )


def object_exists(client, *, bucket: str, object_key: str) -> bool:
    """Idempotent job lifecycle (Reliability mission Batch A): before
    re-running Claude on a redelivered message, the bridge checks whether
    the job's deterministic artifact key was already written by a prior
    attempt (e.g. the process died after upload but before the Job row
    was marked COMPLETED/acked) — reconciling from here rather than
    invoking Claude a second time."""
    try:
        client.head_object(Bucket=bucket, Key=object_key)
        return True
    except ClientError:
        return False


def upload_artifact(
    client, *, bucket: str, object_key: str, src_path: pathlib.Path, content_type: str = "application/json"
) -> str:
    """§27 step 14: upload the sandbox's structured output to
    `noc-job-artifacts`. Returns the sha256 of the uploaded bytes so the
    caller can record it alongside the job."""
    client.upload_file(
        str(src_path), bucket, object_key, ExtraArgs={"ContentType": content_type}
    )
    return sha256_of_file(src_path)


def upload_artifact_metadata(
    client, *, bucket: str, object_key: str, src_path: pathlib.Path,
    content_type: str = "application/json",
) -> dict[str, str | int | None]:
    """Upload an artifact and return the immutable storage identity.

    ``upload_artifact`` remains a checksum-only compatibility helper. Report
    preview artifacts additionally need the exact version returned by the
    versioned reports bucket so the API can pin historical previews.
    """
    client.upload_file(
        str(src_path), bucket, object_key, ExtraArgs={"ContentType": content_type}
    )
    return read_artifact_metadata(client, bucket=bucket, object_key=object_key)


def read_artifact_metadata(client, *, bucket: str, object_key: str) -> dict[str, str | int | None]:
    """Read an artifact's exact current version and checksum.

    This is used by crash reconciliation.  A successful ``HEAD`` alone is
    not enough: the bridge must persist the version that was actually
    recovered and verify the bytes before declaring the job complete.
    """
    metadata = client.head_object(Bucket=bucket, Key=object_key)
    version_id = metadata.get("VersionId")
    params = {"Bucket": bucket, "Key": object_key}
    if version_id:
        params["VersionId"] = version_id
    body = client.get_object(**params)["Body"].read()
    return {
        "bucket": bucket,
        "object_key": object_key,
        "version_id": version_id,
        "sha256": hashlib.sha256(body).hexdigest(),
        "byte_size": len(body),
        "content_type": metadata.get("ContentType"),
    }
