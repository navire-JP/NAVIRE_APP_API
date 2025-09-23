from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import files, qcm
from core.config import ALLOWED_ORIGINS

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="NAVIRE_APP_API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # en prod, resserrer
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

app.include_router(files.router)
app.include_router(qcm.router)

@app.get("/health")
def health():
    return {"status": "ok"}
