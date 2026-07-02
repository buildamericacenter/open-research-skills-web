import mimetypes
import os
from pathlib import Path, PurePosixPath


def use_s3_storage() -> bool:
    return os.environ.get("USE_S3_STORAGE", "").strip().lower() in {"1", "true", "yes", "on"}


def s3_bucket_name() -> str:
    bucket = os.environ.get("S3_BUCKET_NAME", "").strip()
    if not bucket:
        raise RuntimeError("S3_BUCKET_NAME is required when USE_S3_STORAGE is true.")
    return bucket


def s3_client():
    import boto3

    region = os.environ.get("AWS_REGION", "").strip() or None
    return boto3.client("s3", region_name=region)


def safe_s3_path(*parts: str) -> str:
    cleaned_parts: list[str] = []
    for part in parts:
        value = str(part).replace("\\", "/").strip("/")
        if not value:
            continue
        path = PurePosixPath(value)
        if path.is_absolute() or any(piece in {"", ".", ".."} for piece in path.parts):
            raise ValueError("Unsafe S3 object path.")
        cleaned_parts.extend(path.parts)
    return "/".join(cleaned_parts)


def upload_file_to_s3(local_path: Path, key: str) -> str:
    content_type, _ = mimetypes.guess_type(local_path.name)
    extra_args = {"ContentType": content_type} if content_type else None
    if extra_args:
        s3_client().upload_file(str(local_path), s3_bucket_name(), key, ExtraArgs=extra_args)
    else:
        s3_client().upload_file(str(local_path), s3_bucket_name(), key)
    return key


def upload_directory_to_s3(folder: Path, prefix: str) -> list[dict[str, str]]:
    uploaded_files: list[dict[str, str]] = []
    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        relative_path = path.relative_to(folder).as_posix()
        key = safe_s3_path(prefix, relative_path)
        upload_file_to_s3(path, key)
        uploaded_files.append({"filename": relative_path, "s3_key": key})
    return uploaded_files


def presigned_download_url(key: str, filename: str | None = None, expires_in: int = 3600) -> str:
    params = {"Bucket": s3_bucket_name(), "Key": key}
    if filename:
        params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
    return s3_client().generate_presigned_url("get_object", Params=params, ExpiresIn=expires_in)
