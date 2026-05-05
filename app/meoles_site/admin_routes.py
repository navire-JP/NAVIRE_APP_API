from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.meoles_site.meoles_models import CartSession
from app.meoles_site.cart import PRODUCTS

router = APIRouter(prefix="/meoles/admin", tags=["meoles-admin"])


@router.get("/carts")
def get_carts(db: Session = Depends(get_db)):
    sessions = (
        db.query(CartSession)
        .order_by(CartSession.created_at.desc())
        .limit(200)
        .all()
    )
    result = []
    for s in sessions:
        total = sum(
            PRODUCTS[i.product_key]["price"] * i.quantity
            for i in s.items
            if i.product_key in PRODUCTS
        )
        result.append({
            "session_id":  s.session_id,
            "status":      s.status,
            "created_at":  s.created_at.isoformat(),
            "updated_at":  s.updated_at.isoformat(),
            "items":       [{"product_key": i.product_key, "quantity": i.quantity} for i in s.items],
            "total_cents": total,
        })
    return {"sessions": result}