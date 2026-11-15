# Embeds Kitly (site Framer Kitly)

Embeds du second site Framer, branchés sur les routes `/kitly/*` de l'API
(voir `app/kitly/README.md`). Totalement séparés des embeds NAVIRE : jeton
`kitly_token`, DA noir et blanc (Inter + Instrument Serif), aucune donnée NAVIRE.

| Fichier | Page Framer | Composant | Rôle |
|---|---|---|---|
| `connexion.html` | `/connexion` | Embed HTML, largeur Fill, hauteur auto (l'embed agrandit son iframe) | Embed 4 : connexion, inscription (CGU et confidentialité en cases séparées), code de vérification email (collage du code accepté, « plus tard » possible), mot de passe oublié, écran « déjà connecté ». `?mode=inscription` ouvre l'inscription (boutons « Créer ma vitrine » de la landing) |
| `builder.html` | `/dashboard` | Embed HTML, largeur Fill, **hauteur 100vh** (ou Fill dans une section pleine hauteur, sous le header) | « Ma vitrine » : 4 étapes (Identité, Liens, Boutique, Style), aperçu téléphone en direct, sauvegarde automatique, ajout d'offres par type, publication avec confirmation d'email sur place. Mobile : onglets Modifier / Aperçu |

## Réglages (bloc CONFIG en haut de chaque script)

- `API_BASE` : `https://navire-app-api.onrender.com`
- `connexion.html` : `BUILDER_URL` (destination après connexion), `CGU_URL`, `PRIVACY_URL`
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
de Custom. Le rendu de l'aperçu (`renderVitrine`) servira de base à l'embed de la
page profil publique.
