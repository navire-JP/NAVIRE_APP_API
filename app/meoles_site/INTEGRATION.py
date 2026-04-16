# ─── Dans le main.py NAVIRE, ajouter ces 3 lignes ───────────────────────────
# (le reste du fichier NAVIRE ne change pas)

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app", "meoles_site"))

from app.meoles_site.main import meoles_app
app.mount("/", meoles_app)  # "app" = ton instance FastAPI NAVIRE existante

# ─── Variables d'environnement à ajouter sur Render ──────────────────────────
# (dans le même service navire-app-api, dashboard Render > Environment)
#
# STRIPE_SECRET_KEY        → déjà présente sur NAVIRE ✓ (réutilisée)
# STRIPE_WEBHOOK_SECRET    → créer un NOUVEAU webhook Stripe pour MEOLES
#                            URL : https://navire-app-api.onrender.com/meoles/checkout/webhook
#                            Event : checkout.session.completed
#                            → copier le signing secret ici
# BREVO_API_KEY            → déjà présente sur NAVIRE ✓ (réutilisée)
#
# ─── Stripe Dashboard — Webhook à créer ──────────────────────────────────────
# https://dashboard.stripe.com/webhooks/create
# Endpoint URL : https://navire-app-api.onrender.com/meoles/checkout/webhook
# Events       : checkout.session.completed

# ─── Routes disponibles après déploiement ────────────────────────────────────
# GET    /meoles/health
# GET    /meoles/cart                          → lire le panier
# POST   /meoles/cart/add                      → { product_key, quantity }
# POST   /meoles/cart/update                   → { product_key, quantity }
# DELETE /meoles/cart/remove/{product_key}     → supprimer un item
# POST   /meoles/checkout/create-session       → → URL Stripe Checkout
# POST   /meoles/checkout/webhook              → webhook Stripe (privé)

# ─── product_keys catalogue ──────────────────────────────────────────────────
# meoles_custom
# bague_fluid
# collier_polaris
# collier_silence
# tee_s
# tee_m
# tee_l
