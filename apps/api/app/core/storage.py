"""
MinIO (S3-compatible) object storage client — master plan §25.

Three buckets, matching the plan exactly:
- noc-evidence: permanent incident evidence, versioning ON.
- noc-reports: generated reports, versioning ON.
- noc-job-artifacts: execution artifacts, shorter-lived (rule 6/7/15:
  never route large evidence through RabbitMQ or store blobs in
  PostgreSQL — object storage is the only place these live).

Milestone 9 scope is evidence upload/download/checksums/metadata; the
noc-reports and noc-job-artifacts buckets are provisioned now (so their
key layout is fixed early) but not yet written to — that's Milestones
12-14.
"""

import hashlib
import pathlib
import uuid

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

from app.core.config import settings

ALL_BUCKETS = [
    settings.minio_bucket_evidence,
    settings.minio_bucket_reports,
    settings.minio_bucket_job_artifacts,
]

# Buckets that must have object versioning enabled (master plan §4.4, §25).
VERSIONED_BUCKETS = {settings.minio_bucket_evidence, settings.minio_bucket_reports}


def get_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.minio_endpoint_url,
        aws_access_key_id=settings.minio_access_key,
        aws_secret_access_key=settings.minio_secret_key,
        config=BotoConfig(signature_version="s3v4"),
        region_name="us-east-1",
    )


def ensure_buckets() -> None:
    """Idempotent bucket + versioning setup. Safe to call on every app
    startup, mirroring app/seed.py's approach to role/permission bootstrap."""
    client = get_client()
    for bucket in ALL_BUCKETS:
        try:
            client.head_bucket(Bucket=bucket)
        except ClientError:
            client.create_bucket(Bucket=bucket)
        if bucket in VERSIONED_BUCKETS:
            client.put_bucket_versioning(
                Bucket=bucket, VersioningConfiguration={"Status": "Enabled"}
            )


def evidence_object_key(incident_id: str, evidence_type: str, filename: str, upload_id: uuid.UUID) -> str:
    """Layout from master plan §25: incidents/{incident_id}/{alert|logs|supporting}/..."""
    subdir = {
        "ALERT_SCREENSHOT": "alert",
        "GRAFANA_SCREENSHOT": "alert",
        "LOG": "logs",
        "SUPPORTING_DOCUMENT": "supporting",
        "OTHER": "supporting",
    }.get(evidence_type, "supporting")
    # The upload intent, not the original filename, is the object identity.
    # The basename is retained only for operator-friendly storage browsing.
    safe_filename = pathlib.PurePath(filename.replace("\\", "/")).name.replace("/", "_")
    return f"incidents/{incident_id}/{subdir}/{upload_id}-{safe_filename}"


def presigned_upload_url(bucket: str, key: str, content_type: str | None = None) -> str:
    params: dict = {"Bucket": bucket, "Key": key}
    if content_type:
        params["ContentType"] = content_type
    return get_client().generate_presigned_url(
        "put_object", Params=params, ExpiresIn=settings.minio_presigned_url_expiry_seconds
    )


def presigned_download_url(bucket: str, key: str, *, version_id: str | None = None) -> str:
    params: dict = {"Bucket": bucket, "Key": key}
    if version_id:
        params["VersionId"] = version_id
    return get_client().generate_presigned_url(
        "get_object",
        Params=params,
        ExpiresIn=settings.minio_presigned_url_expiry_seconds,
    )


def head_object(bucket: str, key: str, *, version_id: str | None = None) -> dict:
    """Raises botocore.exceptions.ClientError if the object doesn't exist —
    used to verify an upload actually landed before recording metadata."""
    params: dict = {"Bucket": bucket, "Key": key}
    if version_id:
        params["VersionId"] = version_id
    return get_client().head_object(**params)


def delete_object(bucket: str, key: str, *, version_id: str | None = None) -> None:
    params: dict = {"Bucket": bucket, "Key": key}
    if version_id:
        params["VersionId"] = version_id
    get_client().delete_object(**params)


def get_object_bytes(bucket: str, key: str, *, version_id: str | None = None) -> bytes:
    params: dict = {"Bucket": bucket, "Key": key}
    if version_id:
        params["VersionId"] = version_id
    return get_client().get_object(**params)["Body"].read()


def sha256_of_object(bucket: str, key: str, *, version_id: str | None = None) -> str:
    """Hash the exact storage version without trusting client metadata."""
    params: dict = {"Bucket": bucket, "Key": key}
    if version_id:
        params["VersionId"] = version_id
    body = get_client().get_object(**params)["Body"]
    digest = hashlib.sha256()
    for chunk in iter(lambda: body.read(65536), b""):
        digest.update(chunk)
    return digest.hexdigest()


def sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def bucket_status(bucket: str) -> dict:
    """Milestone 17 gap follow-up (Storage tab): real per-bucket status for
    the Admin page — object count, total size, and whether versioning is
    actually on (§25 requires it for noc-evidence/noc-reports). Object
    count/size come from a full listing, which is fine at NOC-report-
    builder scale; if a bucket ever grows large enough for this to matter,
    switch to MinIO's admin API/metrics instead of a client-side sum."""
    client = get_client()
    versioning = client.get_bucket_versioning(Bucket=bucket)
    object_count = 0
    total_bytes = 0
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            object_count += 1
            total_bytes += obj["Size"]
    return {
        "bucket": bucket,
        "versioning_enabled": versioning.get("Status") == "Enabled",
        "object_count": object_count,
        "total_bytes": total_bytes,
    }
