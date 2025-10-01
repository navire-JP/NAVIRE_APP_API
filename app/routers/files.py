from pathlib import Path
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, status
from starlette.status import HTTP_201_CREATED
from pydantic import BaseModel

from app.core.deps import get_storage_service, get_settings_dep
from app.core.security import get_api_key
from app.models.files import FileListResponse, UploadResponse, DeleteResponse, FileInfo
from app.services.storage import StorageService

router = APIRouter(prefix="/files", tags=["files"])


@router.get("", response_model=FileListResponse)
def list_files(storage: StorageService = Depends(get_storage_service)):
    files = storage.list_files()
    return FileListResponse(files=files)


@router.post("/upload", response_model=UploadResponse, status_code=HTTP_201_CREATED)
def upload_file(
    file: UploadFile = File(...),
    storage: StorageService = Depends(get_storage_service),
    settings = Depends(get_settings_dep),
    _: str = Depends(get_api_key),  # protège l'upload par API key
):
    info: FileInfo = storage.save_file(file)

    # Tenter de détecter le nombre de pages (optionnel V1)
    try:
        from pypdf import PdfReader
        pdf_path = Path(settings.STORAGE_PATH) / f"{info.id}.pdf"
        if pdf_path.exists():
            pages = len(PdfReader(str(pdf_path)).pages)
            return UploadResponse(
                id=info.id,
                name=info.name,
                size=info.size,
                pages=pages
            )
    except Exception:
        pass

    return UploadResponse(id=info.id, name=info.name, size=info.size, pages=0)


# Option B : Delete avec body JSON et status 200
class DeleteResponse(BaseModel):
    ok: bool
    id: str | None = None
    message: str | None = None


@router.delete("/{file_id}", response_model=DeleteResponse, status_code=status.HTTP_200_OK)
def delete_file(
    file_id: str,
    storage: StorageService = Depends(get_storage_service),
):
    deleted = storage.delete(file_id)

    if not deleted:
        raise HTTPException(status_code=404, detail="File not found")

    return DeleteResponse(ok=True, id=file_id, message="Deleted")
