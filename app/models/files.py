from typing import Optional
from pydantic import BaseModel, Field


class FileInfo(BaseModel):
    id: str = Field(..., description="Identifiant unique (ex: hash ou uuid)")
    name: str = Field(..., description="Nom du fichier avec extension")
    size: int = Field(..., ge=0, description="Taille en octets")
    pages: int = Field(..., ge=0, description="Nombre de pages détectées")


class FileListResponse(BaseModel):
    files: list[FileInfo]


class UploadResponse(BaseModel):
    id: str
    name: str
    size: int
    pages: int


class DeleteResponse(BaseModel):
    ok: bool = True
    id: Optional[str] = None
