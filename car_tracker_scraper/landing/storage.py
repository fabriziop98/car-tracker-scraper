"""Landing zone: guardar SIEMPRE el HTML/JSON crudo en S3/MinIO, particionado
por fuente/fecha (wdxtkg30nm). "Este unico habito te va a salvar por lo
menos tres veces el primer ano" - si se encuentra un bug de parseo, se
reprocesa desde ac, sin volver a scrapear.

MinIO habla la misma API que S3 (boto3 sirve para ambos) - pasar a S3/R2
real el dia de manana es solo cambiar el endpoint_url, no el codigo.
"""
from __future__ import annotations

import gzip
import os
import uuid
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError


class LandingZoneStorage:
    def __init__(
        self,
        endpoint_url: str | None,
        access_key: str,
        secret_key: str,
        bucket: str,
        region: str = "us-east-1",
    ):
        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError:
            self.client.create_bucket(Bucket=self.bucket)

    def upload_raw(self, source: str, content: bytes, extension: str, fetched_at: datetime | None = None) -> str:
        """Comprime `content` con gzip y lo sube particionado `{source}/{YYYY-MM-DD}/...`.
        Devuelve el s3_key resultante (lo que despues va a `raw_payload.s3_key`
        en Postgres, del lado de la ingesta Spring Boot)."""
        fetched_at = fetched_at or datetime.now(timezone.utc)
        key = (
            f"{source}/{fetched_at:%Y-%m-%d}/"
            f"{fetched_at:%H%M%S}_{uuid.uuid4().hex[:8]}.{extension}.gz"
        )
        self.client.put_object(Bucket=self.bucket, Key=key, Body=gzip.compress(content))
        return key


def storage_from_env() -> LandingZoneStorage:
    return LandingZoneStorage(
        endpoint_url=os.environ.get("LANDING_S3_ENDPOINT_URL") or None,
        access_key=os.environ.get("LANDING_S3_ACCESS_KEY", "car_tracker"),
        secret_key=os.environ.get("LANDING_S3_SECRET_KEY", "car_tracker_minio"),
        bucket=os.environ.get("LANDING_S3_BUCKET", "car-tracker-raw"),
    )
