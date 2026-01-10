import json
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, HTTPException, UploadFile, File, Depends
from fastapi.responses import FileResponse

from api.src.docuform.docx_content_controls import read_content_controls_detailed
from pydantic import BaseModel
from api.src.docuform.models import (
    FieldSource,
    FieldSchema,
    TemplateSchema,
    BulkFieldSourceUpdate,
    FieldSourceUpdate,
)
from api.src.utils.clerk import verify_serniacapital_user, SerniaUser, get_verified_primary_email

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


def _get_schema_path(doc_path: Path) -> Path:
    """Get the schema JSON sidecar path for a document.

    Format: {stem}_schema.json
    """
    return doc_path.parent / f"{doc_path.stem}_schema.json"


def _load_schema(doc_path: Path) -> TemplateSchema | None:
    """Load schema from sidecar JSON file if it exists."""
    schema_path = _get_schema_path(doc_path)
    if not schema_path.exists():
        return None
    try:
        data = json.loads(schema_path.read_text())
        return TemplateSchema(**data)
    except Exception:
        return None


def _save_schema(doc_path: Path, schema: TemplateSchema) -> None:
    """Save schema to sidecar JSON file."""
    schema_path = _get_schema_path(doc_path)
    schema.updated_at = datetime.now(timezone.utc)
    schema_path.write_text(schema.model_dump_json(indent=2))


def _generate_schema_from_controls(filename: str, controls: list[dict]) -> TemplateSchema:
    """Generate a default schema from content controls.

    All fields default to 'client' source. The attorney can override later.
    """
    fields = []
    for i, ctrl in enumerate(controls):
        tag = ctrl.get("tag", "")
        alias = ctrl.get("alias", "")
        value = ctrl.get("value", "")

        # Generate display name from alias or tag
        display_name = alias or tag.replace(".", " ").replace("_", " ").title()

        fields.append(FieldSchema(
            tag=tag,
            alias=alias,
            display_name=display_name,
            source=FieldSource.CLIENT,  # Default all to client
            current_value=value,
            order=i,
        ))

    return TemplateSchema(
        template_filename=filename,
        fields=fields,
    )


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

        # Load schema if it exists, to include field sources
        schema = _load_schema(original_path)
        if schema:
            # Merge source info from schema into controls
            source_map = {f.tag: f.source.value for f in schema.fields}
            for ctrl in controls:
                ctrl["source"] = source_map.get(ctrl.get("tag", ""), "client")
        else:
            # Default all to client
            for ctrl in controls:
                ctrl["source"] = "client"

        return {
            "filename": filename,
            "content_controls": controls,
            "is_working_copy": is_working_copy,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse content controls: {str(e)}")


@router.get("/documents/{filename}/schema")
async def get_document_schema(filename: str, mode: str = "original", conversation_id: str | None = None):
    """
    Get the schema for a document template.

    Returns field definitions with sources (client/attorney/ai) and metadata.
    If no schema exists, generates one from content controls with all fields defaulting to 'client'.

    Args:
        filename: The document filename (must include .docx extension)
        mode: "original" or "working" to get schema based on working copy fields
        conversation_id: Required when mode="working"
    """
    if _is_working_copy(filename) or _is_temp_file(filename):
        raise HTTPException(status_code=400, detail="Cannot access schema for working copies directly")

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

    # Load existing schema or generate from controls
    schema = _load_schema(original_path)

    if not schema:
        # Generate schema from content controls
        file_path = original_path
        if mode == "working":
            working_path = _get_working_copy_path(original_path, conversation_id)
            if working_path.exists():
                file_path = working_path

        controls = read_content_controls_detailed(str(file_path))
        schema = _generate_schema_from_controls(filename, controls)

    return {
        "filename": filename,
        "schema": schema.model_dump(),
    }


@router.put("/documents/{filename}/schema/sources")
async def update_field_sources(
    filename: str,
    updates: BulkFieldSourceUpdate,
    user: SerniaUser,
    conversation_id: str | None = None,
):
    """
    Update field sources for a document template.

    This allows the attorney to override which fields come from client/attorney/ai.
    Changes are saved to the schema sidecar file.

    Args:
        filename: The document filename
        updates: List of {tag, source} pairs to update
        conversation_id: Optional conversation ID (for context)
    """
    if _is_working_copy(filename) or _is_temp_file(filename):
        raise HTTPException(status_code=400, detail="Cannot update schema for working copies directly")

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

    # Load or generate schema
    schema = _load_schema(original_path)
    if not schema:
        # Generate from current controls
        file_path = original_path
        if conversation_id:
            working_path = _get_working_copy_path(original_path, conversation_id)
            if working_path.exists():
                file_path = working_path
        controls = read_content_controls_detailed(str(file_path))
        schema = _generate_schema_from_controls(filename, controls)

    # Apply updates
    update_map = {u.tag: u.source for u in updates.updates}
    for field in schema.fields:
        if field.tag in update_map:
            field.source = update_map[field.tag]

    # Update metadata
    schema.updated_at = datetime.now(timezone.utc)
    if user:
        schema.created_by = get_verified_primary_email(user)

    # Save schema
    _save_schema(original_path, schema)

    return {
        "message": f"Updated {len(updates.updates)} field source(s)",
        "filename": filename,
        "schema": schema.model_dump(),
    }


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


class SaveDocumentRequest(BaseModel):
    """Optional body for save endpoints to include field sources."""
    field_sources: list[FieldSourceUpdate] | None = None


@router.post("/documents/{filename}/save")
async def save_document_over_original(
    filename: str,
    user: SerniaUser,
    conversation_id: str | None = None,
    body: SaveDocumentRequest | None = None,
):
    """
    Save the working copy over the original document.

    This copies the conversation-scoped working copy over the original file.
    The working copy is created when the AI agent modifies the document.
    Optionally saves field sources to the schema.

    Args:
        filename: Original document filename
        conversation_id: Required to identify the conversation-scoped working copy
        body: Optional request body with field_sources to save
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

        # Save field sources if provided
        if body and body.field_sources:
            # Load or generate schema from the saved document
            controls = read_content_controls_detailed(str(original_path))
            schema = _load_schema(original_path) or _generate_schema_from_controls(filename, controls)

            # Apply source updates
            source_map = {u.tag: u.source for u in body.field_sources}
            for field in schema.fields:
                if field.tag in source_map:
                    field.source = source_map[field.tag]

            schema.updated_at = datetime.now(timezone.utc)
            if user:
                schema.created_by = get_verified_primary_email(user)

            _save_schema(original_path, schema)

        return {
            "message": f"Saved changes to '{filename}'",
            "filename": filename,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save document: {str(e)}")


@router.post("/documents/{filename}/save-as")
async def save_document_as_new(
    filename: str,
    new_filename: str,
    user: SerniaUser,
    conversation_id: str | None = None,
    body: SaveDocumentRequest | None = None,
):
    """
    Save the working copy as a new document.

    Args:
        filename: Original document filename
        new_filename: New filename for the saved document (query param)
        conversation_id: Required to identify the conversation-scoped working copy
        body: Optional request body with field_sources to save
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

        # Save field sources if provided
        if body and body.field_sources:
            # Generate schema from the new document
            controls = read_content_controls_detailed(str(new_path))
            schema = _generate_schema_from_controls(new_filename, controls)

            # Apply source updates
            source_map = {u.tag: u.source for u in body.field_sources}
            for field in schema.fields:
                if field.tag in source_map:
                    field.source = source_map[field.tag]

            schema.updated_at = datetime.now(timezone.utc)
            if user:
                schema.created_by = get_verified_primary_email(user)

            _save_schema(new_path, schema)

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
