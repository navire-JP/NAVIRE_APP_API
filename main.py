# main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import files, qcm  # assure-toi que ces modules existent

app = FastAPI(title="NAVIRE_APP_API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # en prod, restreins
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

# --- Health checks ---
@app.get("/", tags=["health"])
def root():
    return {"status": "ok"}  # Render peut checker '/'

@app.get("/health", tags=["health"])
def health():
    return {"status": "ok"}  # et/ou '/health' si tu préfères

# --- Routers ---
app.include_router(files.router)
app.include_router(qcm.router)
