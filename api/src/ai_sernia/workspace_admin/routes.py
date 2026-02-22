"""
Workspace admin API for browsing/editing .workspace/ files.

Mounted as a sub-router of the Sernia router, giving endpoints at
/api/ai-sernia/workspace/*.  Auth via SerniaUser (Clerk + email gate).
"""
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from api.src.ai_sernia.config import WORKSPACE_PATH
from api.src.ai_sernia.memory import resolve_safe_path
from api.src.utils.clerk import SerniaUser

router = APIRouter(prefix="/workspace", tags=["sernia", "workspace"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class WriteBody(BaseModel):
    path: str
    content: str


class MkdirBody(BaseModel):
    path: str


class EntryInfo(BaseModel):
    name: str
    type: str  # "file" | "directory"
    size: int | None = None


class LsResponse(BaseModel):
    path: str
    entries: list[EntryInfo]


class ReadResponse(BaseModel):
    path: str
    content: str
    size: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_resolve(relative_path: str) -> Path:
    """Resolve path or raise 400."""
    try:
        return resolve_safe_path(WORKSPACE_PATH, relative_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _safe_resolve_dir(relative_path: str) -> Path:
    """
    Resolve a directory path.  Empty string → workspace root.
    Directories don't have a suffix so resolve_safe_path's suffix check
    is irrelevant, but traversal protection still applies.
    """
    cleaned = relative_path.strip().lstrip("/")
    if not cleaned:
        return WORKSPACE_PATH.resolve()

    resolved = (WORKSPACE_PATH / cleaned).resolve()
    try:
        resolved.relative_to(WORKSPACE_PATH.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Path escapes workspace: {relative_path}")
    return resolved


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/ls", response_model=LsResponse)
async def list_directory(user: SerniaUser, path: str = Query("")) -> LsResponse:
    """List directory contents."""
    target = _safe_resolve_dir(path)

    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Directory not found: {path or '.'}")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {path}")

    entries: list[EntryInfo] = []
    for entry in sorted(target.iterdir()):
        if entry.is_dir():
            entries.append(EntryInfo(name=entry.name, type="directory"))
        else:
            entries.append(EntryInfo(name=entry.name, type="file", size=entry.stat().st_size))

    return LsResponse(path=path or ".", entries=entries)


@router.get("/read", response_model=ReadResponse)
async def read_file(user: SerniaUser, path: str = Query(...)) -> ReadResponse:
    """Read file content."""
    resolved = _safe_resolve(path)

    if not resolved.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    if not resolved.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {path}")

    content = resolved.read_text(encoding="utf-8")
    return ReadResponse(path=path, content=content, size=len(content))


@router.post("/write")
async def write_file(user: SerniaUser, body: WriteBody):
    """Create or overwrite a file."""
    resolved = _safe_resolve(body.path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(body.content, encoding="utf-8")
    return {"path": body.path, "size": len(body.content)}


@router.post("/mkdir")
async def make_directory(user: SerniaUser, body: MkdirBody):
    """Create a directory (including parents)."""
    target = _safe_resolve_dir(body.path)
    target.mkdir(parents=True, exist_ok=True)
    return {"path": body.path}


@router.delete("/delete")
async def delete_path(user: SerniaUser, path: str = Query(...)):
    """Delete a file or empty directory."""
    resolved = _safe_resolve(path)

    if not resolved.exists():
        # Also try as directory (no suffix check needed)
        resolved = _safe_resolve_dir(path)
        if not resolved.exists():
            raise HTTPException(status_code=404, detail=f"Not found: {path}")

    if resolved.is_dir():
        if any(resolved.iterdir()):
            raise HTTPException(status_code=400, detail="Directory is not empty")
        resolved.rmdir()
    else:
        resolved.unlink()

    return {"path": path, "deleted": True}


@router.get("/download")
async def download_file(user: SerniaUser, path: str = Query(...)):
    """Download a file as an attachment."""
    resolved = _safe_resolve(path)

    if not resolved.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    if not resolved.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {path}")

    return FileResponse(
        path=resolved,
        filename=resolved.name,
        media_type="application/octet-stream",
    )
