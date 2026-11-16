# Embeds Kitly (site Framer Kitly)

Embeds du second site Framer, branchés sur les routes `/kitly/*` de l'API
(voir `app/kitly/README.md`). Totalement séparés des embeds NAVIRE : jeton
`kitly_token`, DA noir et blanc (Inter + Instrument Serif), aucune donnée NAVIRE.

| Fichier | Page Framer | Composant | Rôle |
|---|---|---|---|
| `home.html` | Home (`/`) | Embed HTML, largeur Fill, hauteur auto | Accueil simple : « Crée ta vitrine » en haut, adapté à la personne (visiteur : « Créer ma vitrine gratuitement » + 3 étapes ; connecté : progression et « Continuer ma vitrine » ; vitrine en ligne : lien, visites, demandes, Gérer / Voir / Copier), puis la marketplace complète en dessous |
| `connexion.html` | `/connexion` | Embed HTML, largeur Fill, hauteur auto (l'embed agrandit son iframe) | Embed 4 : connexion, inscription (CGU et confidentialité en cases séparées), code de vérification email (collage du code accepté, « plus tard » possible), mot de passe oublié, écran « déjà connecté ». `?mode=inscription` ouvre l'inscription (boutons « Créer ma vitrine » de la landing) |
| `profil.html` | `/profile?u=<adresse>` (et/ou page 404 de Framer pour `kitly.fr/<adresse>`) | Embed HTML, largeur Fill, hauteur auto | Embed 2 : vitrine publique. Lit l'adresse dans `?u=` ou dans le chemin (`kitly.fr/lea-martin`). Fiches offres, contact / devis / rendez-vous, réalisations, CGV du vendeur, signalement, comptage des visites. Modales placées sur la partie visible de la page. Adresse inconnue : « Cette vitrine n'existe pas » |
| `marketplace.html` | `/marketplace` (et section « marketplace » de la landing en mode `showcase`) | Embed HTML, largeur Fill, hauteur auto | Embed 3 : recherche (sans accents), catégories, lieu, type d'offre, budget, tri ; « Les nouveaux sur Kitly », « Les profils populaires », encart « Sponsorisé » séparé du classement, « Voir plus », « Comment sont classés les résultats ? ». Filtres recopiés dans l'URL de la page (lien partageable). Les cartes ouvrent la vitrine avec `?src=marketplace`. Mode `showcase` : 8 profils + bouton vers la marketplace |
| `builder.html` | `/builder` | Embed HTML, largeur Fill, **hauteur 100vh** (ou Fill dans une section pleine hauteur, sous le header) | « Ma vitrine » : 4 étapes (Identité, Liens, Boutique, Style), aperçu téléphone en direct, sauvegarde automatique, ajout d'offres par type, publication avec confirmation d'email sur place. Mobile : onglets Modifier / Aperçu |

## Moteur de rendu partagé

La vitrine est dessinée par **un seul fichier**, `app/kitly/static/vitrine.js`,
servi par l'API (`GET /kitly/static/vitrine.js`) et chargé par `builder.html`
(aperçu), `profil.html` et la page `/kitly/p/<adresse>`. L'aperçu du builder
est donc exactement la vitrine publiée. Pour modifier un template, on ne touche
qu'à ce fichier.

La marketplace suit le même principe : `app/kitly/static/marketplace.js`
(`GET /kitly/static/marketplace.js`), chargé par `marketplace.html` et `home.html`.

**Ne pas garder** l'embed NAVIRE `embeds/home.html` dans le projet Framer Kitly :
il appelle l'API NAVIRE. La page `/home` reçoit `embeds/kitly/home.html`.

## Servir `kitly.fr/<adresse>` : trois options

Framer ne sait pas créer une page pour chaque adresse de freelance. Au choix :

1. **Recommandé — proxy devant Framer** (ex. Cloudflare Worker, gratuit) : les
   chemins qui ne sont pas des pages Framer sont servis par
   `https://navire-app-api.onrender.com/kitly/p/<adresse>`. La page est rendue
   par l'API avec ses balises de partage : **aperçu avec photo et nom quand le
   lien est collé sur WhatsApp, LinkedIn, Instagram**, réponse 200 bien
   référencée, chargement le plus rapide. C'est aussi ce qui permettra les
   domaines personnalisés.
2. **Sans infrastructure — page 404 de Framer** : y placer `profil.html`, qui lit
   l'adresse dans le chemin. Fonctionne pour les visiteurs, mais Framer répond
   « 404 » (mauvais pour Google) et les liens partagés n'ont pas d'aperçu.
3. **Page Framer `/profile`** avec `profil.html` et des liens `kitly.fr/profile?u=<adresse>`
   (réglage par défaut des embeds et de `KITLY_PROFILE_URL_TEMPLATE`) : simple, mais URL moins
   belle, mêmes limites que l'option 2.

Les embeds sont réglés par défaut sur l'option 3 (`/profile?u=`). L'option 1 est à
mettre en place avant le lancement (changer alors `PROFILE_URL` / `PUBLIC_BASE` et
`KITLY_PROFILE_URL_TEMPLATE`).

## Réglages (bloc CONFIG en haut de chaque script)

Les liens vers les pages du site sont **relatifs** (`/builder`, `/profile?u=…`) et
complétés automatiquement avec l'adresse du site qui affiche l'embed : les mêmes
fichiers fonctionnent sur `https://kitly.framer.website` et sur `https://kitly.fr`,
sans modification. Une URL complète (`https://…`) reste possible pour forcer un lien.

- `API_BASE` : `https://navire-app-api.onrender.com`
- `connexion.html` : `BUILDER_URL` (destination après connexion), `CGU_URL`, `PRIVACY_URL`
- `profil.html` : `KITLY_URL`, `PRIVACY_URL` (citée par le formulaire de contact), `MARKETPLACE_URL`,
  `RESERVED` (chemins du site qui ne sont jamais des vitrines)
- `home.html` : `REGISTER_URL`, `LOGIN_URL`, `BUILDER_URL`, `PRICING_URL`, `PUBLIC_BASE`, `PROFILE_URL`
- `marketplace.html` : `MODE` (`full` / `showcase`), `PROFILE_URL` (lien d'une vitrine, `{slug}` remplacé :
  `https://kitly.fr/{slug}` avec l'option 1 ou 2 ci-dessus, `https://kitly.fr/vitrine?u={slug}` avec l'option 3),
  `MARKETPLACE_URL`, `REGISTER_URL`, `PAGE_SIZE`, `SHOWCASE_COUNT`
- `builder.html` : `LOGIN_URL`, `PRICING_URL`, `LEGAL_URL` (future page CGV vendeur),
  `PUBLIC_BASE` (vitrine publique = `PUBLIC_BASE` + slug)

Le domaine du site Kitly doit figurer dans `CORS_ORIGINS` sur Render.

## Messages envoyés à la page

`connexion.html` émet `{type: "KITLY_LOGGED_IN", token, user}` et
`{type: "KITLY_LOGGED_OUT"}` (postMessage vers la page), pour un futur badge de
compte dans le header.

## Templates de vitrine

Blanc, Noir, Cadre, Éditorial (toutes formules, couleurs fixes) ; Bold (fond
coloré à formes, gros titre, boutons pilule à icône — d'après l'inspiration) et
Studio, avec couleurs, typographie des titres et fond personnalisables, à partir
de Custom.
