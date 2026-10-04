"""File storage helpers: safe randomized filenames, type/size validation."""
import hashlib
import os
import re
import shutil
import uuid

from ..core.config import settings

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}


def _upload_root() -> str:
    root = settings.UPLOAD_DIR or os.path.abspath(
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "storage")
    )
    os.makedirs(root, exist_ok=True)
    return root


def validate_upload(filename: str, size: int) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"File type not allowed. Permitted: PDF, PNG, JPG/JPEG")
    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    if size <= 0:
        raise ValueError("Empty file")
    if size > max_bytes:
        raise ValueError(f"File exceeds {settings.MAX_UPLOAD_MB}MB limit")
    return ext


def save_upload(content: bytes, original_name: str, kind: str = "docs") -> tuple[str, str]:
    """Returns (safe_filename, sha256_hex)."""
    ext = validate_upload(original_name, len(content))
    safe_name = f"{uuid.uuid4().hex}{ext}"
    dest_dir = os.path.join(_upload_root(), kind)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, safe_name)
    with open(dest, "wb") as f:
        f.write(content)
    digest = hashlib.sha256(content).hexdigest()
    return safe_name, digest


def resolve_path(kind: str, filename: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "", filename or "")
    path = os.path.realpath(os.path.join(_upload_root(), kind, safe))
    if not path.startswith(os.path.realpath(_upload_root())) or not os.path.isfile(path):
        raise FileNotFoundError("File not found")
    return path


def file_exists(kind: str, filename: str) -> bool:
    try:
        resolve_path(kind, filename)
        return True
    except FileNotFoundError:
        return False
