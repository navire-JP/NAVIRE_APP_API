# KITLY — API

Kitly est hébergé dans l'API NAVIRE (même process, même base PostgreSQL) mais
**totalement séparé** :

| | NAVIRE | Kitly |
|---|---|---|
| Code | `app/routers`, `app/services`… | `app/kitly/` uniquement |
| Routes | `/auth`, `/users`… | `/kitly/...` |
| Tables | `users`, `subscriptions`… | `kitly_*` (aucune clé étrangère vers NAVIRE) |
| Comptes | `users` | `kitly_users` (une même adresse peut avoir deux comptes indépendants) |
| Jetons | `JWT_SECRET`, sans audience | `KITLY_JWT_SECRET`, audience `kitly` : un jeton ne passe jamais d'un produit à l'autre |
| Stripe | `STRIPE_SECRET_KEY` (variable globale `stripe.api_key`) | `KITLY_STRIPE_SECRET_KEY` via un `StripeClient` dédié, jamais la variable globale |
| Emails | expéditeur NAVIRE | expéditeur `KITLY_SENDER_EMAIL` |
| Images | `navire/…` | `kitly/u<id>/…` (compte Cloudinary distinct possible) |
| Admin | console NAVIRE | comptes Kitly `is_admin` (`KITLY_ADMIN_EMAILS`) |

Branchement dans `app/main.py` : un import, `kitly_startup()` au démarrage,
`kitly_purge_job` dans le scheduler, `app.include_router(kitly_router)`.

## Variables d'environnement (Render)

| Variable | Obligatoire | Rôle |
|---|---|---|
| `KITLY_JWT_SECRET` | oui | Clé des jetons Kitly (à défaut : dérivée de `JWT_SECRET`, différente de celle-ci) |
| `KITLY_STRIPE_SECRET_KEY` | oui | Clé secrète du **compte Stripe Kitly** |
| `KITLY_STRIPE_WEBHOOK_SECRET` | oui | Secret du webhook `/kitly/stripe/webhook` |
| `KITLY_STRIPE_CONNECT_WEBHOOK_SECRET` | oui | Secret du webhook `/kitly/stripe/connect-webhook` |
| `KITLY_STRIPE_PRICE_CUSTOM` | oui | Price ID mensuel Custom (7 €) |
| `KITLY_STRIPE_PRICE_PREMIUM` | oui | Price ID mensuel Premium+ (14,90 €) |
| `KITLY_SENDER_EMAIL` / `KITLY_SENDER_NAME` | oui | Expéditeur validé chez Brevo (défaut `no-reply@kitly.fr` / `Kitly`) |
| `KITLY_BREVO_API_KEY` | non | Clé Brevo dédiée (à défaut : `BREVO_API_KEY`) |
| `KITLY_CLOUDINARY_URL` | non | Compte Cloudinary dédié (à défaut : `CLOUDINARY_URL`, dossier `kitly/`) |
| `KITLY_FRONTEND_URL` | non | Site Framer Kitly (défaut `https://kitly.framer.website` ; passer à `https://kitly.fr` une fois le domaine relié). Liens des emails et des pages servies par l'API. Les retours Stripe suivent automatiquement le domaine du visiteur (s'il figure dans `KITLY_CORS_ORIGINS`), sauf si `KITLY_BILLING_*` / `KITLY_CONNECT_*` sont fixées |
| `KITLY_CORS_ORIGINS` | non | Origines autorisées sur `/kitly/*`, séparées par des virgules (défaut : kitly.fr, www.kitly.fr, kitly.framer.website) |
| `KITLY_PUBLIC_HOST` | non | Domaine des vitrines (défaut `kitly.fr`) |
| `KITLY_PROFILE_URL_TEMPLATE` | non | Adresse publique d'une vitrine, `{slug}` remplacé (défaut `<KITLY_FRONTEND_URL>/profile?u={slug}`) |
| `KITLY_PRIVACY_URL` | non | Politique de confidentialité citée par le formulaire de contact des vitrines |
| `KITLY_ADMIN_EMAILS` | non | Adresses promues admin Kitly (séparées par des virgules) |
| `KITLY_MARKETPLACE_COMMISSION_BPS` | non | Commission marketplace (défaut 1000 = 10 %) |
| `KITLY_NEW_PROFILES_DAYS` / `KITLY_POPULAR_WINDOW_DAYS` | non | Fenêtres « nouveaux » (7 j) et « populaires » (30 j) |

**CORS séparé** (`app/kitly/cors.py`) : les routes `/kitly/*` n'acceptent que
`KITLY_CORS_ORIGINS` (défaut : `https://kitly.fr,https://www.kitly.fr,https://kitly.framer.website`) ;
les routes NAVIRE gardent `CORS_ORIGINS`, inchangé. Rien à ajouter dans `CORS_ORIGINS` pour Kitly.
Le démarrage affiche `[kitly] ⚠️ Variables manquantes : …` sans bloquer NAVIRE.

## Stripe — mise en place

1. **Créer un compte Stripe dédié « Kitly »** (dans la même organisation Stripe
   que NAVIRE si besoin). C'est ce qui garantit un libellé bancaire différent :
   Settings → Public details → *Statement descriptor* = `KITLY`, nom public
   Kitly, branding Kitly sur Checkout et sur l'onboarding Connect.
2. Produits : *Kitly Custom* (7 €/mois) et *Kitly Premium+* (14,90 €/mois),
   prix TTC → `KITLY_STRIPE_PRICE_*`.
3. Webhook « compte » → `https://navire-app-api.onrender.com/kitly/stripe/webhook`,
   événements : `checkout.session.completed`, `customer.subscription.created`,
   `customer.subscription.updated`, `customer.subscription.deleted`, `invoice.payment_failed`.
4. Activer **Connect** (plateforme / marketplace), puis un webhook
   « comptes connectés » → `/kitly/stripe/connect-webhook`, événements :
   `account.updated`, `account.application.deauthorized`.
5. Portail client (Settings → Billing → Customer portal) : autoriser
   changement de carte, factures, résiliation.

Les comptes connectés sont créés avec frais et pertes à la charge de la
plateforme et le tableau de bord Express (`controller`). **À valider** selon
le rôle juridique retenu pour Kitly avant la mise en production.

## Routes par embed

Authentification : en-tête `Authorization: Bearer <token>`. Côté front, stocker
le jeton sous `kitly_token` (jamais `navire_token`).

**Embed 4 — Login / Register / compte**
`POST /kitly/auth/register` · `POST /kitly/auth/login` · `POST /kitly/auth/verify-email` ·
`POST /kitly/auth/resend-code` · `POST /kitly/auth/password/forgot` · `POST /kitly/auth/password/reset` ·
`POST /kitly/auth/logout` · `POST /kitly/auth/logout-all` · `GET /kitly/auth/me` (compte, formule,
limites, checklist d'onboarding) · `PATCH /kitly/account` · `POST /kitly/account/password` ·
`GET /kitly/account/export` · `DELETE /kitly/account`

**Tableau de bord — Ma vitrine** (création progressive, autosave)
`GET /kitly/slugs/check?slug=` · `POST|GET|PATCH /kitly/me/profile` · `POST /kitly/me/profile/publish` ·
`POST /kitly/me/profile/unpublish` · `POST /kitly/me/media` (multipart `file`, `purpose`) ·
`/kitly/me/portfolio[/{id}|/reorder]` · `/kitly/me/offers[/{id}|/reorder]` ·
`GET|PATCH /kitly/me/leads` · `GET /kitly/me/stats?days=30`

**Embed 2 — Page profil publique**
`GET /kitly/public/profiles/{slug}` · `POST …/{slug}/view` (à appeler une fois à l'affichage,
`{"source": "marketplace"}` si on vient de la marketplace) · `GET …/{slug}/offers/{id}` ·
`GET …/{slug}/portfolio/{id}` · `GET …/{slug}/terms[?version=N]` · `POST …/{slug}/leads` ·
`POST …/{slug}/report` · `GET /kitly/public/domains/{host}`

**Pages servies par l'API**
`GET /kitly/p/{slug}` — vitrine complète (balises de partage Open Graph, canonical, contenu
lisible sans JavaScript, 404 « noindex » si non publiée) · `GET /kitly/static/vitrine.js` — moteur
de rendu partagé (page, embed profil, aperçu du builder) · `GET /kitly/static/marketplace.js` — marketplace
partagée (embeds marketplace et accueil)

**Embed 3 — Marketplace**
`GET /kitly/marketplace/new` · `/popular` · `/categories` ·
`/search?q=&category=&location=&kind=&budget_max_cents=&sort=relevance|new|popular|price_asc&page=` ·
`/ranking-info` — les profils `sponsored` sont renvoyés à part et doivent être étiquetés « Sponsorisé ».

**Embed 5 — Subscribe / paiements** (`embeds/kitly/subscribe.html` ; retours Stripe vers `/subscribe` ;
les erreurs Stripe sont rendues en 502 `{"code": "stripe_error", "message": …}`)
`GET /kitly/plans` · `GET /kitly/billing/subscription` · `POST /kitly/billing/checkout {"plan"}`
(URL Stripe, ou changement de formule sur place si déjà abonné) · `POST /kitly/billing/portal` ·
`POST /kitly/billing/cancel` · `POST /kitly/billing/resume` · `GET /kitly/billing/invoices` ·
`POST /kitly/connect/onboarding` · `GET /kitly/connect/status[?refresh=true]` ·
`POST /kitly/connect/dashboard-link`

**Mes documents juridiques**
`GET /kitly/legal/questionnaire` · `GET|PUT /kitly/me/legal-profile` · `GET /kitly/me/legal-answers` ·
`PUT /kitly/me/legal-answers/{scope}` (refus 422 des réponses contraires aux droits impératifs) ·
`GET /kitly/me/terms` · `PUT|DELETE /kitly/me/terms/draft` · `POST /kitly/me/terms/draft/publish` ·
`GET /kitly/me/terms/versions/{id}`

**Référentiels / back-office**
`GET /kitly/catalog` · `/kitly/admin/overview` · `/kitly/admin/plans[/{code}]` (limites
configurables) · `/kitly/admin/reports` · `/kitly/admin/profiles/{id}/suspend|unsuspend`

## Choix de conception

- **Offres** : une table pour les 4 catégories (`physical`, `digital`, `service`,
  `appointment`) ; les champs propres à chaque catégorie sont dans `details`,
  validés par un schéma par catégorie (`schemas.DETAILS_MODELS`).
- **CGV vendeur** : versions immuables une fois publiées (empreinte SHA-256),
  ancienne version « remplacée » mais toujours consultable ; `kitly_orders` et
  `kitly_terms_acceptances` pointent vers la version acceptée. Le générateur
  automatique viendra remplir les brouillons (`generator_version`).
- **Commission** : chaque commande porte son `channel` (`direct` / `marketplace`) ;
  seule la marketplace est commissionnée.
- **Statistiques** : une visite = un visiteur par jour (empreinte salée IP +
  navigateur + jour, aucune IP stockée), robots et propriétaire exclus.
- **Formules** : la base fait foi (`kitly_plans`), modifiable depuis le back-office.
  Une formule perdue désactive les fonctions sans supprimer de données.
- **Front** : tous les textes renvoyés sont saisis par les freelances, à
  afficher échappés (`textContent`, jamais `innerHTML`).

## Reste à faire (sprints suivants)

- Parcours d'achat (Stripe Connect, destination charges + commission) et
  acceptation des CGV au paiement (modèles déjà en place).
- Rendez-vous : disponibilités, calendrier, réservations.
- Générateur de CGV (modèles d'articles à faire valider par un juriste).
- Vérification DNS des domaines personnalisés (enregistrement TXT) avant activation.
- Impressions marketplace, avis vérifiés, déclarations DAC7.
