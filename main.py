from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import files, qcm
from core.config import ALLOWED_ORIGINS  # .env: ALLOWED_ORIGINS="*"

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

# Routers
app.include_router(files.router)
app.include_router(qcm.router)
