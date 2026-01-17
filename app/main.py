from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import APP_NAME, APP_VERSION, CORS_ORIGINS
from app.db.database import Base, engine
from app.db import models 
from app.routers.auth import router as auth_router

app = FastAPI(title=APP_NAME, version=APP_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS if CORS_ORIGINS else ["*"],
    allow_credentials=False,  
    allow_methods=["*"],
    allow_headers=["*"],
)


Base.metadata.create_all(bind=engine)
app.include_router(auth_router)

@app.get("/health")
def health():
    return {"ok": True}
