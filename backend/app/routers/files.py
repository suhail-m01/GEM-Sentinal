import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import get_current_user
from ..models import Document, User
from ..services.files import file_exists, resolve_path

router = APIRouter(prefix="/files", tags=["files"])


KINDS = {
    "docs": "docs",
    "clarifications": "clarifications",
    "evidence": "evidence",
    "trusted": "trusted",
}


@router.get("/{kind}/{filename}")
def download_file(kind: str, filename: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if kind not in KINDS:
        raise HTTPException(status_code=404, detail="Not found")
    if not file_exists(kind, filename):
        raise HTTPException(status_code=404, detail="File not found")
    # access control: bidder files belong to the bidder (or their applications);
    # officers/admins may inspect files of applications in scope (prototype: any officer/admin)
    if user.role == "BIDDER":
        doc = db.query(Document).filter(Document.file_path == filename, Document.user_id == user.id).first()
        if not doc:
            raise HTTPException(status_code=404, detail="File not found")
    path = resolve_path(kind, filename)
    ext = os.path.splitext(path)[1].lower()
    media = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(ext, "application/octet-stream")
    return Response(content=open(path, "rb").read(), media_type=media,
                    headers={"Content-Disposition": f'inline; filename="{os.path.basename(path)}"'})
