from pathlib import Path
from fastapi import APIRouter, HTTPException, UploadFile, File, Depends
from fastapi.responses import FileResponse

from api.src.docuform.docx_content_controls import read_content_controls_detailed
from api.src.utils.clerk import verify_serniacapital_user, SerniaUser

# Router-level auth: all routes require @serniacapital.com user
# If a route needs the user object, add `user: SerniaUser` - FastAPI caches the dependency
router = APIRouter(
    prefix="/docuform",
    tags=["docuform"],
    dependencies=[Depends(verify_serniacapital_user)],
)

# Include sub-routers (hierarchical routing pattern)
from api.src.docuform.template_agent.routes import router as template_agent_router
router.include_router(template_agent_router)

# Directory containing DOCX documents (templates and filled documents)
DOCUMENTS_DIR = Path(__file__).parent / "documents"


def _is_working_copy(filename: str) -> bool:
    """Check if filename is a working copy (contains _template_agent_working)."""
    return "_template_agent_working" in filename


def _is_temp_file(filename: str) -> bool:
    """Check if filename is a temp file (starts with ~$)."""
    return filename.startswith("~$")


def _get_working_copy_path(original_path: Path, conversation_id: str | None = None) -> Path:
    """Get the working copy path for an original document.

    If conversation_id is provided, returns conversation-scoped working copy.
    Format: {stem}_template_agent_working_{short_id}.docx
    """
    if conversation_id:
        short_id = conversation_id[:8]
        return original_path.parent / f"{original_path.stem}_template_agent_working_{short_id}.docx"
    return original_path.parent / f"{original_path.stem}_template_agent_working.docx"


@router.get("/documents")
async def list_documents():
    """List available DOCX documents (templates and filled documents).

    Excludes working copies (*_working.docx) and temp files (~$*).
    Includes metadata about whether a working copy exists for each document.
    """
    documents = []
    for file in DOCUMENTS_DIR.glob("*.docx"):
        # Skip working copies and temp files
        if _is_working_copy(file.name) or _is_temp_file(file.name):
            continue

        # Check if working copy exists
        working_copy_path = _get_working_copy_path(file)

        documents.append({
            "name": file.stem,
            "filename": file.name,
            "has_working_copy": working_copy_path.exists(),
        })
    return {"documents": documents}


@router.get("/documents/{filename}")
async def get_document(filename: str, mode: str = "original", conversation_id: str | None = None):
    """
    Serve a DOCX document file.

    Args:
        filename: The document filename (must include .docx extension)
        mode: "original" (default) or "working" to get the working copy
        conversation_id: Required when mode="working" to get the conversation-scoped working copy

    If mode="working" and no working copy exists, falls back to original.
    """
    # Block direct access to working copies and temp files
    if _is_working_copy(filename) or _is_temp_file(filename):
        raise HTTPException(status_code=400, detail="Cannot directly access working copies or temp files")

    if not filename.endswith(".docx"):
        raise HTTPException(status_code=400, detail="Only .docx files are allowed")

    original_path = DOCUMENTS_DIR / filename

    # Security: prevent path traversal
    try:
        original_path = original_path.resolve()
        if not str(original_path).startswith(str(DOCUMENTS_DIR.resolve())):
            raise HTTPException(status_code=403, detail="Access denied")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid filename")

    if not original_path.exists():
        raise HTTPException(status_code=404, detail=f"Document '{filename}' not found")

    # Determine which file to serve
    if mode == "working":
        working_path = _get_working_copy_path(original_path, conversation_id)
        if working_path.exists():
            return FileResponse(
                path=working_path,
                filename=working_path.name,
                media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                headers={"X-Working-Copy": "true"},
            )
        # Fall back to original if no working copy
        return FileResponse(
            path=original_path,
            filename=filename,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"X-Working-Copy": "false"},
        )

    return FileResponse(
        path=original_path,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@router.get("/documents/{filename}/content-controls")
async def get_document_content_controls(filename: str, mode: str = "original", conversation_id: str | None = None):
    """
    Extract content controls (SDT elements) from a DOCX document.

    Args:
        filename: The document filename (must include .docx extension)
        mode: "original" (default) or "working" to get controls from working copy
        conversation_id: Required when mode="working" to get the conversation-scoped working copy

    Returns a mapping of tag -> value for all content controls in the document.
    This can be used to highlight content controls in the rendered preview.
    """
    # Block direct access to working copies and temp files
    if _is_working_copy(filename) or _is_temp_file(filename):
        raise HTTPException(status_code=400, detail="Cannot directly access working copies or temp files")

    if not filename.endswith(".docx"):
        raise HTTPException(status_code=400, detail="Only .docx files are allowed")

    original_path = DOCUMENTS_DIR / filename

    try:
        original_path = original_path.resolve()
        if not str(original_path).startswith(str(DOCUMENTS_DIR.resolve())):
            raise HTTPException(status_code=403, detail="Access denied")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid filename")

    if not original_path.exists():
        raise HTTPException(status_code=404, detail=f"Document '{filename}' not found")

    # Determine which file to read
    file_path = original_path
    is_working_copy = False
    if mode == "working":
        working_path = _get_working_copy_path(original_path, conversation_id)
        if working_path.exists():
            file_path = working_path
            is_working_copy = True

    try:
        controls = read_content_controls_detailed(str(file_path))
        return {
            "filename": filename,
            "content_controls": controls,
            "is_working_copy": is_working_copy,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse content controls: {str(e)}")


@router.post("/documents/upload")
async def upload_document(file: UploadFile = File(...)):
    """
    Upload a DOCX document (template) to the documents folder.

    Returns the uploaded document's metadata and content controls.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    if not file.filename.endswith(".docx"):
        raise HTTPException(status_code=400, detail="Only .docx files are allowed")

    # Sanitize filename to prevent path traversal
    safe_filename = Path(file.filename).name
    if safe_filename != file.filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    file_path = DOCUMENTS_DIR / safe_filename

    # Check if file already exists
    if file_path.exists():
        raise HTTPException(
            status_code=409,
            detail=f"Document '{safe_filename}' already exists. Delete it first or use a different name."
        )

    try:
        # Save the uploaded file
        content = await file.read()
        file_path.write_bytes(content)

        # Extract content controls from the uploaded document
        controls = read_content_controls_detailed(str(file_path))

        return {
            "message": "Document uploaded successfully",
            "filename": safe_filename,
            "name": file_path.stem,
            "content_controls": controls,
            "content_control_count": len(controls),
        }
    except Exception as e:
        # Clean up if something went wrong
        if file_path.exists():
            file_path.unlink()
        raise HTTPException(status_code=500, detail=f"Failed to upload document: {str(e)}")


@router.delete("/documents/{filename}")
async def delete_document(filename: str):
    """
    Delete a DOCX document from the documents folder.
    """
    if not filename.endswith(".docx"):
        raise HTTPException(status_code=400, detail="Only .docx files are allowed")

    file_path = DOCUMENTS_DIR / filename

    # Security: prevent path traversal
    try:
        file_path = file_path.resolve()
        if not str(file_path).startswith(str(DOCUMENTS_DIR.resolve())):
            raise HTTPException(status_code=403, detail="Access denied")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid filename")

    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Document '{filename}' not found")

    try:
        file_path.unlink()
        return {"message": f"Document '{filename}' deleted successfully", "filename": filename}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete document: {str(e)}")


@router.post("/documents/{filename}/save")
async def save_document_over_original(filename: str, conversation_id: str | None = None):
    """
    Save the working copy over the original document.

    This copies the conversation-scoped working copy over the original file.
    The working copy is created when the AI agent modifies the document.

    Args:
        filename: Original document filename
        conversation_id: Required to identify the conversation-scoped working copy
    """
    if not filename.endswith(".docx"):
        raise HTTPException(status_code=400, detail="Only .docx files are allowed")

    # Construct paths
    original_path = DOCUMENTS_DIR / filename
    working_path = _get_working_copy_path(original_path, conversation_id)

    # Security: prevent path traversal
    try:
        original_path = original_path.resolve()
        working_path = working_path.resolve()
        if not str(original_path).startswith(str(DOCUMENTS_DIR.resolve())):
            raise HTTPException(status_code=403, detail="Access denied")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Check working copy exists
    if not working_path.exists():
        raise HTTPException(status_code=404, detail="No working copy to save. Make modifications first.")

    # Check original exists
    if not original_path.exists():
        raise HTTPException(status_code=404, detail=f"Original document '{filename}' not found")

    try:
        import shutil
        # Copy working copy over original
        shutil.copy2(str(working_path), str(original_path))
        # Remove working copy
        working_path.unlink()
        return {
            "message": f"Saved changes to '{filename}'",
            "filename": filename,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save document: {str(e)}")


@router.post("/documents/{filename}/save-as")
async def save_document_as_new(filename: str, new_filename: str, conversation_id: str | None = None):
    """
    Save the working copy as a new document.

    Args:
        filename: Original document filename
        new_filename: New filename for the saved document (query param)
        conversation_id: Required to identify the conversation-scoped working copy
    """
    if not filename.endswith(".docx"):
        raise HTTPException(status_code=400, detail="Only .docx files are allowed")

    if not new_filename.endswith(".docx"):
        new_filename = f"{new_filename}.docx"

    # Construct paths
    original_path = DOCUMENTS_DIR / filename
    working_path = _get_working_copy_path(original_path, conversation_id)
    new_path = DOCUMENTS_DIR / new_filename

    # Security: prevent path traversal
    try:
        working_path = working_path.resolve()
        new_path = new_path.resolve()
        if not str(working_path).startswith(str(DOCUMENTS_DIR.resolve())):
            raise HTTPException(status_code=403, detail="Access denied")
        if not str(new_path).startswith(str(DOCUMENTS_DIR.resolve())):
            raise HTTPException(status_code=403, detail="Access denied")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Check working copy exists
    if not working_path.exists():
        raise HTTPException(status_code=404, detail="No working copy to save. Make modifications first.")

    # Check new filename doesn't already exist
    if new_path.exists():
        raise HTTPException(status_code=409, detail=f"Document '{new_filename}' already exists")

    try:
        import shutil
        # Copy working copy to new file
        shutil.copy2(str(working_path), str(new_path))
        return {
            "message": f"Saved as '{new_filename}'",
            "filename": new_filename,
            "original_filename": filename,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save document: {str(e)}")


@router.delete("/documents/{filename}/working")
async def delete_working_copy(filename: str, conversation_id: str | None = None):
    """
    Delete the working copy of a document (reset modifications).

    Args:
        filename: Original document filename
        conversation_id: Required to identify the conversation-scoped working copy
    """
    if not filename.endswith(".docx"):
        raise HTTPException(status_code=400, detail="Only .docx files are allowed")

    original_path = DOCUMENTS_DIR / filename
    working_path = _get_working_copy_path(original_path, conversation_id)

    # Security: prevent path traversal
    try:
        working_path = working_path.resolve()
        if not str(working_path).startswith(str(DOCUMENTS_DIR.resolve())):
            raise HTTPException(status_code=403, detail="Access denied")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid filename")

    if not working_path.exists():
        return {"message": "No working copy exists", "filename": filename}

    try:
        working_path.unlink()
        return {"message": f"Working copy deleted for '{filename}'", "filename": filename}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete working copy: {str(e)}")
