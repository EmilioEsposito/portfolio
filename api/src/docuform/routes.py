from pathlib import Path
from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import FileResponse

from api.src.docuform.docx_content_controls import read_content_controls_detailed

router = APIRouter(prefix="/docuform", tags=["docuform"])

# Directory containing DOCX documents (templates and filled documents)
DOCUMENTS_DIR = Path(__file__).parent / "documents"


@router.get("/documents")
async def list_documents():
    """List available DOCX documents (templates and filled documents)."""
    documents = []
    for file in DOCUMENTS_DIR.glob("*.docx"):
        documents.append({
            "name": file.stem,
            "filename": file.name,
        })
    return {"documents": documents}


@router.get("/documents/{filename}")
async def get_document(filename: str):
    """
    Serve a DOCX document file.

    The filename should include the .docx extension.
    """
    # Security: only allow .docx files from the documents directory
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

    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@router.get("/documents/{filename}/content-controls")
async def get_document_content_controls(filename: str):
    """
    Extract content controls (SDT elements) from a DOCX document.

    Returns a mapping of tag -> value for all content controls in the document.
    This can be used to highlight content controls in the rendered preview.
    """
    if not filename.endswith(".docx"):
        raise HTTPException(status_code=400, detail="Only .docx files are allowed")

    file_path = DOCUMENTS_DIR / filename

    try:
        file_path = file_path.resolve()
        if not str(file_path).startswith(str(DOCUMENTS_DIR.resolve())):
            raise HTTPException(status_code=403, detail="Access denied")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid filename")

    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Document '{filename}' not found")

    try:
        controls = read_content_controls_detailed(str(file_path))
        return {
            "filename": filename,
            "content_controls": controls
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
