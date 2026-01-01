from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(prefix="/docuform", tags=["docuform"])

# Directory containing DOCX documents (templates and filled documents)
DOCUMENTS_DIR = Path(__file__).parent


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
