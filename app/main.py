from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.db.database import Base, engine
from app.routers.auth import router as auth_router

app = FastAPI(title="NAVIRE API V1")

# CORS (Framer)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # on resserrera ensuite
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

Base.metadata.create_all(bind=engine)

app.include_router(auth_router)

@app.get("/health")
def health():
    return {"ok": True}
