"""S3-compatible storage upload."""

from __future__ import annotations

import boto3
import httpx
from botocore.client import Config as BotoConfig

from app.config import get_settings


def get_s3_client():
    settings = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url or None,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=BotoConfig(signature_version="s3v4"),
    )


def upload_file(local_path: str, job_id: str, output_filename: str) -> str:
    """
    Upload a local file to S3 and return its public URL.
    Object key: {prefix}/{job_id}/{output_filename}
    """
    settings = get_settings()
    s3 = get_s3_client()

    key = f"{settings.s3_output_prefix}/{job_id}/{output_filename}"
    s3.upload_file(
        local_path,
        settings.s3_bucket,
        key,
        ExtraArgs={"ContentType": _content_type(output_filename)},
    )

    # Build public URL — приоритет: S3_PUBLIC_URL > endpoint > AWS
    if settings.s3_public_url:
        base = settings.s3_public_url.rstrip("/")
        url = f"{base}/{key}"
    elif settings.s3_endpoint_url:
        base = settings.s3_endpoint_url.rstrip("/")
        url = f"{base}/{settings.s3_bucket}/{key}"
    else:
        url = f"https://{settings.s3_bucket}.s3.amazonaws.com/{key}"

    return url


def upload_to_presigned_url(local_path: str, presigned_url: str) -> str:
    """
    Upload a local file to a caller-supplied S3 presigned URL via HTTP PUT.
    Returns the base URL (without query-string / presigned params) as the
    canonical result URL so the caller knows where the object lives.
    """
    filename = local_path.rsplit("/", 1)[-1]
    content_type = _content_type(filename)

    with open(local_path, "rb") as fh:
        data = fh.read()

    with httpx.Client(timeout=120) as client:
        resp = client.put(
            presigned_url,
            content=data,
            headers={"Content-Type": content_type},
        )
        resp.raise_for_status()

    # Strip presigned query params — return clean object URL
    base_url = presigned_url.split("?")[0]
    return base_url


def _content_type(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower()
    return {
        "mp4": "video/mp4",
        "mp3": "audio/mpeg",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "zip": "application/zip",
        "m3u8": "application/vnd.apple.mpegurl",
        "ts": "video/mp2t",
    }.get(ext, "application/octet-stream")
