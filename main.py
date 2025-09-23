from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import files, qcm
from core.config import ALLOWED_ORIGINS

app = FastAPI(title="NAVIRE Light API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(files.router, prefix="/files", tags=["Files"])
app.include_router(qcm.router, prefix="/qcm", tags=["QCM"])

@app.get("/health")
def health():
    return {"status": "ok"}
