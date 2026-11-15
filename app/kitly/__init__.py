"""
KITLY — plateforme de vitrines pour freelances, hébergée dans l'API NAVIRE.

Tout le code Kitly vit dans ce paquet. Il ne partage avec NAVIRE que
l'infrastructure (process FastAPI, base de données, moteur SQLAlchemy) :

  - comptes séparés (table kitly_users, jetons JWT signés avec une clé et une
    audience propres : un jeton NAVIRE n'ouvre rien chez Kitly, et l'inverse) ;
  - Stripe séparé (clés KITLY_STRIPE_*, client StripeClient dédié, jamais la
    variable globale stripe.api_key que NAVIRE et MEOLES se disputent) ;
  - emails envoyés au nom de Kitly (expéditeur KITLY_*) ;
  - routes sous le préfixe /kitly, tables préfixées kitly_.

Point d'entrée : app.kitly.router.router (inclus par app/main.py).
Documentation : app/kitly/README.md.
"""
