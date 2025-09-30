import os
import shutil
import uuid
from pathlib import Path
from typing import List

from fastapi import UploadFile, HTTPException
from starlette.status import HTTP_400_BAD_REQUEST, HTTP_413_REQUEST_ENTITY_TOO_LARGE, HTTP_415_UNSUPPORTED_MEDIA_TYPE

from app.models.files import FileInfo


class StorageService:
    """
    Service de gestion des fichiers stockés localement.
    (V1 simple — extensible à S3 ou autre)
    """

    def __init__(self, base_path: str = "./storage", max_upload_mb: int = 25):
        self.base_path = Path(base_path)
        self.max_upload_bytes = max_upload_mb * 1024 * 1024
        self.base_path.mkdir(parents=True, exist_ok=True)

    def list_files(self) -> List[FileInfo]:
        """
        Liste les fichiers présents dans le dossier storage.
        """
        files: List[FileInfo] = []
        for f in self.base_path.glob("*.pdf"):
            size = f.stat().st_size
            # Pour l’instant pages = 0 (on calculera via utils/pdf_extract)
            files.append(FileInfo(id=f.stem, name=f.name, size=size, pages=0))
        return files

    def save_file(self, file: UploadFile) -> FileInfo:
        """
        Sauvegarde un fichier PDF uploadé.
        """
        if not file.filename.lower().endswith(".pdf"):
            raise HTTPException(
                status_code=HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Seuls les fichiers PDF sont acceptés",
            )

        contents = file.file.read()
        if len(contents) > self.max_upload_bytes:
            raise HTTPException(
                status_code=HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Fichier trop volumineux (max {self.max_upload_bytes // (1024*1024)} MB)",
            )

        file_id = str(uuid.uuid4())
        dest_path = self.base_path / f"{file_id}.pdf"

        with open(dest_path, "wb") as f:
            f.write(contents)

        size = dest_path.stat().st_size
        # Pages sera calculé plus tard (utils/pdf_extract)
        return FileInfo(id=file_id, name=file.filename, size=size, pages=0)

    def delete_file(self, file_id: str) -> bool:
        """
        Supprime un fichier à partir de son ID.
        """
        path = self.base_path / f"{file_id}.pdf"
        if not path.exists():
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail="Fichier introuvable",
            )

        path.unlink()
        return True

    def clear_all(self):
        """
        Supprime tous les fichiers du dossier (utile pour les tests).
        """
        shutil.rmtree(self.base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
