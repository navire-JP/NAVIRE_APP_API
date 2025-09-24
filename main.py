# main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from core.config import ALLOWED_ORIGINS
from routers import files, qcm

app = FastAPI(title="NAVIRE_APP_API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"ok": True}

app.include_router(files.router)
app.include_router(qcm.router)
