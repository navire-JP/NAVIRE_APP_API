# ─── Dans le main.py NAVIRE, remplacer/adapter ces lignes ───────────────────
# (le reste du fichier NAVIRE ne change pas)

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app", "meoles_site"))

from app.meoles_site.stripe_routes import router as meoles_stripe_router
from app.meoles_site.custom_routes  import router as meoles_custom_router
from app.meoles_site.admin_routes   import router as meoles_admin_router

# "app" = ton instance FastAPI NAVIRE existante
app.include_router(meoles_stripe_router)
app.include_router(meoles_custom_router)
app.include_router(meoles_admin_router)

# ─── Fichiers supprimés / devenus inutiles ────────────────────────────────────
#
#   cart.py          → supprimé  (plus de panier côté backend)
#   cart_routes.py   → supprimé  (idem)
#   meoles_models.py → supprimé  (tables CartSession / CartItem inutiles)
#                                 ⚠ si Supabase/Alembic est actif, drop les
#                                   tables meoles_cart_sessions et meoles_cart_items
#
# ─── Variables d'environnement sur Render ─────────────────────────────────────
#
#   STRIPE_SECRET_KEY_MEOLES      → clé secrète Stripe compte MEOLES
#   STRIPE_WEBHOOK_SECRET_MEOLES  → signing secret du webhook ci-dessous
#   BREVO_API_KEY_MEOLES          → clé API Brevo
#
# ─── Webhook Stripe à configurer ──────────────────────────────────────────────
#
#   Dashboard → https://dashboard.stripe.com/webhooks/create
#   URL       : https://navire-app-api.onrender.com/meoles/checkout/webhook
#   Event     : checkout.session.completed
#
#   Ce webhook reçoit TOUS les paiements MEOLES :
#   - Payment Links fixes (bague, colliers, t-shirts)
#   - Flow MEOLES CUSTOM (via custom_routes.py → Stripe Checkout)
#
# ─── Routes disponibles après déploiement ────────────────────────────────────
#
#   POST   /meoles/checkout/webhook          → webhook Stripe (privé)
#   POST   /meoles/custom/submit             → soumission formulaire custom
#   GET    /meoles/admin/orders              → liste commandes (?limit=20&starting_after=...)
#   GET    /meoles/admin/orders/{session_id} → détail commande