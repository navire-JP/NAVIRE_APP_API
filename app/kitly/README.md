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
| Admin | console NAVIRE | console Kitly `/admin` (embed `admin.html`), comptes `is_admin` (`KITLY_ADMIN_EMAILS`) |

Branchement dans `app/main.py` : un import, `kitly_startup()` au démarrage,
`kitly_purge_job` dans le scheduler, `app.include_router(kitly_router)`.

## Variables d'environnement (Render)

| Variable | Obligatoire | Rôle |
|---|---|---|
| `KITLY_JWT_SECRET` | oui | Clé des jetons Kitly (à défaut : dérivée de `JWT_SECRET`, différente de celle-ci) |
| `KITLY_STRIPE_SECRET_KEY` | oui | Clé secrète du **compte Stripe Kitly** |
| `KITLY_STRIPE_WEBHOOK_SECRET` | oui | Secret du webhook `/kitly/stripe/webhook` |
| `KITLY_STRIPE_CONNECT_WEBHOOK_SECRET` | oui | Secret du webhook `/kitly/stripe/connect-webhook` |
| `KITLY_STRIPE_PUBLISHABLE_KEY` | oui (panier) | Clé publiable `pk_live_…` du compte Stripe Kitly : formulaire de carte de la page `/checkout`. Sans elle, le panier affiche « paiement pas encore activé » |
| `KITLY_STRIPE_PRICE_CUSTOM` | oui | Price ID mensuel Custom (7 €) |
| `KITLY_STRIPE_PRICE_PREMIUM` | oui | Price ID mensuel Premium+ (14,90 €) |
| `KITLY_SENDER_EMAIL` / `KITLY_SENDER_NAME` | oui | Expéditeur validé chez Brevo (défaut `no-reply@kitly.fr` / `Kitly`) |
| `KITLY_BREVO_API_KEY` | non | Clé Brevo dédiée (à défaut : `BREVO_API_KEY`) |
| `KITLY_CLOUDINARY_URL` | non | Compte Cloudinary dédié (à défaut : `CLOUDINARY_URL`, dossier `kitly/`) |
| `KITLY_FRONTEND_URL` | non | Site Framer Kitly (défaut `https://kitly.framer.website` ; passer à `https://kitly.fr` une fois le domaine relié). Liens des emails et des pages servies par l'API. Les retours Stripe suivent automatiquement le domaine du visiteur (s'il figure dans `KITLY_CORS_ORIGINS`), sauf si `KITLY_BILLING_*` / `KITLY_CONNECT_*` sont fixées |
| `KITLY_CORS_ORIGINS` | non | Origines autorisées sur `/kitly/*`, séparées par des virgules (défaut : kitly.fr, www.kitly.fr, kitly.framer.website) |
| `KITLY_PUBLIC_HOST` | non | Domaine des vitrines (défaut `kitly.fr`) |
| `KITLY_PROFILE_URL_TEMPLATE` | non | Adresse publique d'une vitrine, `{slug}` remplacé (défaut `<KITLY_FRONTEND_URL>/profile#{slug}` ; balises canonical / og:url omises tant que l'adresse contient un `#`) |
| `KITLY_PRIVACY_URL` | non | Politique de confidentialité citée par le formulaire de contact des vitrines |
| `KITLY_ADMIN_EMAILS` | non | Seules adresses autorisées à ouvrir la console `/admin` (séparées par des virgules), par email + mot de passe. Retirer une adresse coupe l'accès aussitôt |
| `KITLY_ADMIN_SESSION_MINUTES` | non | Durée d'une session de la console admin (défaut 480 = 8 h) |
| `KITLY_CONTACT_EMAIL` | non | Adresse citée dans l'email de suspension d'une vitrine pour la contester (défaut : l'adresse de l'admin qui suspend). Conseillé : une adresse d'équipe, ex. `contact@kitly.fr` |
| `KITLY_MARKETPLACE_COMMISSION_BPS` | non | Commission marketplace (défaut 1000 = 10 %) |
| `KITLY_NEW_PROFILES_DAYS` / `KITLY_POPULAR_WINDOW_DAYS` | non | Fenêtres « nouveaux » (7 j) et « populaires » (30 j) |

**Aucune clé affichée** : l'expéditeur n'est montré (console admin, `/kitly/health`, journaux) que s'il s'agit
d'une adresse email ; une clé Brevo collée par erreur dans `KITLY_SENDER_EMAIL` est masquée et signalée, et
toute clé qui apparaîtrait dans une réponse de Brevo est remplacée par `xkeysib-•••`.

**Console admin refusée (« Email ou mot de passe incorrect »)** : la raison exacte est dans Render → Logs
(`[kitly] ⚠️ Connexion admin refusée pour jp***@… : …`) — adresse absente de `KITLY_ADMIN_EMAILS`, aucun compte
Kitly avec cette adresse (le compte Kitly est distinct de NAVIRE : s'inscrire sur `/connexion`), ou mot de passe
incorrect (« Mot de passe oublié » sur `/connexion`). `/kitly/health` indique `admin_emails_configured` (nombre
d'adresses lues). Après 5 échecs, la connexion admin est bloquée 15 minutes.

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
   `customer.subscription.updated`, `customer.subscription.deleted`, `invoice.payment_failed`,
   et pour les ventes sur les vitrines : `checkout.session.expired`,
   `checkout.session.async_payment_succeeded`, `checkout.session.async_payment_failed`,
   `charge.refunded`, `charge.dispute.created`, et pour le panier : `payment_intent.succeeded`,
   `payment_intent.canceled`, `payment_intent.payment_failed`.
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
partagée (embeds marketplace et accueil) · `GET /kitly/static/logo.png` — logo Kitly (fichier `app/kitly/static/logo.png`)

**Embed 3 — Marketplace**
`GET /kitly/marketplace/offers?q=&category=&kind=&sort=relevance|new|price_asc&page=` (offres des vitrines référencées,
avec la vitrine qui les propose) · `/new` · `/popular` · `/categories` ·
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

**Référentiels**
`GET /kitly/catalog` (templates, typos, mises en page `site` / `links`, catégories…)

**Panier multi-vendeurs** (`app/kitly/routers/cart.py`, page `/checkout`)
- `POST /kitly/cart/quote` : récapitulatif du panier (gardé dans le navigateur), revalidé par le serveur, groupé par vendeur ;
  une seule livraison facturée par vendeur (la plus chère des articles expédiés).
- `POST /kitly/checkout` : un PaymentIntent sur le compte Stripe Kitly pour tout le panier (`transfer_group` = référence
  `P…`), une `KitlyOrder` par article chez son vendeur (visible dans « Mes ventes » une fois payée), preuve d'acceptation
  des CGV de chaque vendeur. Renvoie le `client_secret` du formulaire de carte intégré.
- Webhook compte plateforme, **événements à ajouter** : `payment_intent.succeeded`, `payment_intent.canceled`,
  `payment_intent.payment_failed`. Au paiement : commandes payées, stock décrémenté, **un virement (Transfer) par
  vendeur**, net de la commission marketplace, adossé au paiement (`source_transaction`) ; un email par vendeur, un
  récapitulatif à l'acheteur. Remboursement total (`charge.refunded`) : commandes remboursées et virements repris.
  Remboursement partiel : panier « partiellement remboursé », virements à régulariser à la main.
- `GET /kitly/checkout/{ref}` : état du panier (page de confirmation), sans donnée personnelle.
- ⚠️ L'argent du panier transite par le compte Kitly avant d'être reversé (« separate charges and transfers ») :
  rôle de Kitly (agent de paiement / place de marché) à faire valider avant la mise en production.

**Achat sur une vitrine** (`app/kitly/routers/shop.py`)
- Une offre est achetable en ligne (`buyable` dans la vitrine publique) si : action
  « Commander », prix fixe ≥ 0,50 €, produit physique / numérique / prestation, en stock,
  paiements du vendeur actifs (Connect) et CGV du vendeur publiées. Sinon « Commander »
  reste une demande par email.
- `POST /kitly/public/profiles/{slug}/offers/{id}/checkout` : variante, quantité,
  livraison (option ou retrait), acceptation des CGV (preuve dans `kitly_terms_acceptances`),
  renonciation à la rétractation pour un contenu numérique → Stripe Checkout, argent
  versé au vendeur (`transfer_data.destination`), commission
  `KITLY_MARKETPLACE_COMMISSION_BPS` seulement pour une vente venue de la marketplace.
- La commande passe « payée » sur le webhook (`checkout.session.completed`) : stock
  décrémenté, email au vendeur (adresse de livraison) et à l'acheteur (lien de livraison
  pour un produit numérique : `details.delivery_url`, jamais public). Remboursement et
  litige mettent à jour le statut. `GET /kitly/me/orders?status=todo|refunded|…` : ventes du
  freelance et résumé (à traiter, ventes et net sur 30 jours) ; `PATCH /kitly/me/orders/{ref}`
  `{fulfilled, tracking, notify}` : marquée expédiée / prête / traitée, email à l'acheteur (une seule fois).
- CGV : `GET /kitly/me/terms/template` rend un modèle pré-rempli (identité du vendeur,
  types d'offres) ; la publication est refusée tant qu'il reste « [à compléter] ».

**Contenu « site »** : blocs `section` (titre, texte, image à gauche ou à droite, bouton) et
`gallery` (12 images), en plus de `text`, `header`, `image`. Dans la mise en page « site », ces
blocs forment des sections pleine largeur (section `content`, les sections titrées entrent dans le
menu) ; les liens et vidéos restent groupés dans la section `links`. Ordre par défaut des sections :
en-tête, à propos, contenu, offres, boutique, réalisations, liens, contact.

**Fiche produit détaillée** (toutes les catégories d'offres, champs de `details`, tous facultatifs) :
`highlights` (points clés, 10 × 200 car.), `specs` (`[{label, value}]`, 30 lignes : tableau « Caractéristiques »),
`faq` (`[{question, answer}]`, 15), `guarantee` (garantie et retours, 2000 car.). Dans la vitrine, la fiche s'ouvre en
grand (deux colonnes sur ordinateur) : galerie avec vignettes et flèches, prix (mis à jour selon l'option), stock,
points clés, achat, modes de livraison et tarifs, puis description, « Ce qui est inclus » (livrables d'une prestation),
caractéristiques (lignes du vendeur + champs de la catégorie : référence, poids, dimensions, format, licence…), FAQ,
garantie et vendeur. Toute image (fiche, galerie, section, projet) s'ouvre dans une visionneuse plein écran : flèches,
vignettes, clavier (← → Échap), glisser sur téléphone, clic pour zoomer. Le builder édite ces champs, ainsi que
les options (variantes), modes de livraison, zones, référence, poids et dimensions.

**Police personnalisée (Premium+, limite `custom_font`)** : `POST /kitly/me/font` (multipart `file`, WOFF2 / WOFF /
TTF / OTF reconnus à leur signature, 3 Mo max) envoie le fichier sur Cloudinary (fichier brut, dossier `kitly/u<id>/font`)
et rend `{url, format, name}`, à enregistrer dans `theme.custom_font` (`{url, name, format, apply_to: all|headings|body}`)
via `PATCH /kitly/me/profile` ; `{"custom_font": {"url": ""}}` la retire. La police s'applique quel que soit le
template. Une formule sans `custom_font` ne peut ni importer ni enregistrer de police, et la vitrine publique
l'ignore (elle reste enregistrée et revient si le compte repasse en Premium+). La limite est ajoutée
automatiquement aux formules existantes au démarrage ; la ligne « Ta propre police d'écriture sur ta vitrine »
n'apparaît dans la liste des avantages Premium+ qu'une fois ajoutée depuis la console admin (Formules).

**Marketplace — offre de bienvenue** : tout compte de moins de `KITLY_WELCOME_MARKETPLACE_DAYS`
jours (défaut 30, 0 = désactivé) est référencé dans la marketplace quelle que soit sa formule
(s'il n'a pas refusé d'y apparaître) ; `/auth/me` renvoie `marketplace_welcome_until`.

**Formules offertes et comptes créés par l'équipe** : `POST /kitly/admin/users` crée un compte
(formule offerte facultative, durée en jours) et envoie une invitation
(`/connexion?mode=activation&email=…`) : la personne reçoit un code, choisit son mot de passe et
accepte les CGU à ce moment-là. Les formules offertes expirent toutes les 30 minutes
(`kitly_grants_job`), le compte repasse à sa formule payée ou Free.

**Console admin** (`/kitly/admin/*` ; il faut une adresse de `KITLY_ADMIN_EMAILS` ET une
session ouverte par mot de passe sur `POST /kitly/auth/admin-login` (jeton « adm », 8 h).
Une session ordinaire du site, même du même compte, est refusée. Chaque action est
inscrite dans `kitly_admin_actions`)
- `GET /overview` : comptes, vitrines, abonnements, revenu mensuel, demandes, visites, signalements
- Comptes : `GET /users?q=&plan=&page=` · `GET /users/{id}` · `POST|DELETE /users/{id}/grant`
  (formule offerte, durée optionnelle ; formule effective = max(Stripe, offerte), expirée par
  `kitly_purge_job`) · `POST /users/{id}/verify-email` · `/deactivate` (motif) · `/reactivate` ·
  `/logout-all` · `/impersonate` · `DELETE /users/{id}` (`confirm: "SUPPRIMER"` + motif ;
  abonnement Stripe résilié d'abord)
- Vitrines : `GET /profiles?q=&status=published|draft|suspended` · `POST /profiles/{id}/suspend`
  (motif envoyé au titulaire par email, exposé des motifs DSA) · `/unsuspend` · `/unpublish`
- `GET /reports?status=` · `PATCH /reports/{id}?status=` · `GET /plans` · `PATCH /plans/{code}`
- `GET /system` (configuration effective, sans secret) · `POST /system/email-check` ·
  `POST /system/email-test` · `GET /actions` (journal)

**Mode support** (`/impersonate`) : jeton d'1 h au nom du compte, marqué `imp` ; `/auth/me`
renvoie `impersonated_by`. Paiement, portail Stripe, Connect, résiliation, mot de passe,
suppression du compte et « déconnecter partout » restent réservés au titulaire (403).

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

- Rendez-vous : disponibilités, calendrier, réservations.
- Générateur de CGV (modèles d'articles à faire valider par un juriste).
- Vérification DNS des domaines personnalisés (enregistrement TXT) avant activation.
- Impressions marketplace, avis vérifiés, déclarations DAC7.
