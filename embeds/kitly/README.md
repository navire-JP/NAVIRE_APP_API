# Embeds Kitly (site Framer Kitly)

Embeds du second site Framer, branchés sur les routes `/kitly/*` de l'API
(voir `app/kitly/README.md`). Totalement séparés des embeds NAVIRE : jeton
`kitly_token`, DA noir et blanc (Inter + Instrument Serif), aucune donnée NAVIRE.

| Fichier | Page Framer | Composant | Rôle |
|---|---|---|---|
| `home.html` | Home (`/`) | Embed HTML, largeur Fill, hauteur auto | Accueil simple : « Crée ta vitrine » en haut, adapté à la personne (visiteur : « Créer ma vitrine gratuitement » + 3 étapes ; connecté : progression et « Continuer ma vitrine » ; vitrine en ligne : lien, visites, demandes, Gérer / Voir / Copier), puis la marketplace complète en dessous |
| `connexion.html` | `/connexion` | Embed HTML, largeur Fill, hauteur auto (l'embed agrandit son iframe) | Embed 4 : connexion, inscription (CGU et confidentialité en cases séparées), code de vérification email (collage du code accepté, « plus tard » possible), mot de passe oublié, écran « déjà connecté ». `?mode=inscription` ouvre l'inscription (boutons « Créer ma vitrine » de la landing). `?mode=activation&email=…` : activation d'un compte créé par l'équipe (code par email, mot de passe, CGU) |
| `profil.html` | `/profile#<adresse>` (et/ou page 404 de Framer pour `kitly.fr/<adresse>`) — **page Framer sans header ni footer** | Embed HTML, largeur Fill, hauteur auto, collé en haut de la page sans marge | Embed 2 : vitrine publique. Lit l'adresse dans le hash (`/profile#lea-martin`, fiche offre : `#lea-martin/offre-12`) ; les anciens liens `?u=` sont réécrits en `#`, et le chemin (`kitly.fr/lea-martin`) reste accepté. Mise en page façon page de liens : en-tête propre à la personne (sa photo et son nom au défilement, « Partager », « Contacter »), bannière fondue dans le fond, photo de profil, chaque section dans un encart, pied de page CGV · Mentions légales · Confidentialité · Signaler. **Le fond choisi est peint sur toute la page Framer** (couleur, dégradé, formes ou image, et couleur de la barre du navigateur). Fiches offres, achat en ligne (récapitulatif, CGV, paiement Stripe, confirmation au retour), contact / devis / rendez-vous, réalisations, signalement, comptage des visites. Modales placées sur la partie visible de la page. Adresse inconnue : « Cette vitrine n'existe pas » |
| `marketplace.html` | `/marketplace` (et section « marketplace » de la landing en mode `showcase`) | Embed HTML, largeur Fill, hauteur auto | Embed 4, sur le modèle des grandes marketplaces. **Accueil** : logo (`/kitly/static/logo.png`), titre, recherche (résultats pendant la frappe), raccourcis « Populaire » (catégories les plus fournies), grille **Explorer par catégorie** (icône + nombre de profils), **Profils à la une** (les plus consultés), **Dernières offres**, **Nouveaux sur Kitly**, bandeau de confiance (contact direct, paiement sécurisé, panier unique). **Cartes profils détaillées** : bannière, photo, nom, métier, ville · catégorie, début de bio, 2 offres avec prix, « À partir de X € », étiquettes Sponsorisé / Nouveau / Premium+. Sur téléphone les profils de l'accueil défilent en rangée (dans le cadre, la page ne défile jamais horizontalement). **Résultats** (recherche, catégorie, « Tout voir ») : retour à l'accueil, catégories en filtre, tri (pertinence, nouveautés, prix), offres (chaque carte ouvre la fiche sur la vitrine : `/profile?src=marketplace#<adresse>/offre-<id>`) puis profils, « Voir plus ». Recherche, catégorie et tri recopiés dans l'URL (`?q=…&categorie=…&tri=…`). Panier : icône du header du site. Mode `showcase` : cartes détaillées + « Tout voir » |
| `checkout.html` | `/checkout` | Embed HTML, largeur Fill, hauteur auto | Embed 6 : **panier et paiement** (code dans `/kitly/static/checkout.js`). Articles groupés par vendeur (option, quantité, livraison, retirer ; prix et stock recalculés par le serveur ; une seule livraison facturée par vendeur), coordonnées, adresse de livraison (pays livrés par tous les vendeurs du panier), acceptation des CGV de chaque vendeur, renonciation pour le numérique, puis formulaire de carte Stripe intégré (Payment Element : carte, Apple Pay, Google Pay, 3D Secure) et confirmation (`/checkout?ref=P…` au retour de 3D Secure). Le panier vit dans le navigateur (`/kitly/static/cart.js`) : « Ajouter au panier » sur les fiches offres des vitrines, bouton « Panier » flottant sur les vitrines et la marketplace |
| `subscribe.html` | `/subscribe` | Embed HTML, largeur Fill, hauteur auto | Abonnement (Stripe du compte Kitly) : formules lues dans l'API, « Commencer » → inscription puis retour ici formule présélectionnée, paiement Stripe, changement de formule sur place (prorata), résiliation en fin de période / reprise, carte et factures (portail Stripe), alerte d'échec de paiement, « Activer les paiements » (Stripe Connect). Après paiement, « activation en cours » jusqu'à la confirmation de Stripe |
| `builder.html` | `/builder` | Embed HTML, largeur Fill, **hauteur 100vh** (ou Fill dans une section pleine hauteur, sous le header) | « Ma vitrine » : 4 étapes (Identité, Contenu, Boutique, Style), aperçu en direct Mobile / Ordinateur, étape « Contenu » (sections texte + image, galeries, images, titres, liens, vidéos), mise en page « Site complet » ou « Page de liens », sauvegarde automatique, ajout d'offres par type, bloc « Vente en ligne » (activer les paiements, rédiger et publier ses CGV à partir d'un modèle pré-rempli), onglet « Mes ventes » (commandes à traiter, adresse de livraison, marquer comme expédiée / prête / traitée avec numéro de suivi et email au client, montants sur 30 jours, accès à l'espace Stripe), publication avec confirmation d'email sur place. Mobile : onglets Modifier / Aperçu. Bandeau « Mode support » quand l'équipe ouvre le builder d'un compte |
| `admin.html` | `/admin` | Embed HTML, largeur Fill, **hauteur 100vh** (sous le header) | Console admin Kitly, avec son propre écran de connexion (email + mot de passe, adresses de `KITLY_ADMIN_EMAILS` uniquement, session limitée à l'onglet et à 8 h) : vue d'ensemble, comptes (formule offerte, confirmer l'email, déconnecter, désactiver, supprimer, « Ouvrir son builder »), vitrines (suspendre avec motif envoyé au titulaire, rétablir, dépublier), signalements, formules (prix, textes, limites), système (emails, Stripe, CORS) et journal des actions. Page à exclure des moteurs de recherche dans Framer |

## Pages légales (`embeds/kitly/legal/`)

| Fichier | Page Framer |
|---|---|
| `legal/cgu.html` | `/cgu` |
| `legal/cgv.html` | `/cgv` (abonnements Kitly, avec formulaire de rétractation) |
| `legal/mentions-legales.html` | `/mentions-legales` |
| `legal/politique-de-confidentialite.html` | `/politique-de-confidentialite` |

Embed HTML, largeur Fill, hauteur auto ; même charte que la page Tarifs (sommaire
latéral, numérotation). Textes rédigés d'après le fonctionnement réel de Kitly
(données collectées, durées, prestataires, abonnements, classement de la
marketplace, signalement). Les mentions surlignées en gris `[entre crochets]`
(identité de l'éditeur, emails, médiateur, taux de commission…) sont à compléter
avant publication ; l'ensemble est à faire relire par un juriste.

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


**Deux mises en page** (Style → « Mise en page », `theme.layout`) :
- `site` (défaut) : un mini-site. Sur ordinateur : menu fixe (À propos, Prestations, Boutique,
  Réalisations, Contact) qui fait défiler la page Framer, grand en-tête avec photo ou bannière,
  sections pleine largeur, formulaire de contact intégré, pied de page avec mentions légales.
  Sur mobile : une colonne façon page de liens, avec menu burger.
- `links` : la page de liens d'origine, une colonne partout.
Le passage d'une disposition à l'autre suit la largeur réelle de l'embed (≥ 860 px), pas celle
de l'écran. Lien direct vers une offre : `#<adresse>/offre-<id>` (ou `#offre-<id>` sur `kitly.fr/<adresse>`).

**Sur ordinateur (embed ≥ 860 px)** : « Site complet » devient un vrai site — haut de page en deux
colonnes (nom en grand, métier, bio, boutons et réseaux à gauche ; bannière en grande image avec la
photo de profil à droite), barre des sections, sections pleine largeur séparées par des filets
(grilles de 3 ou 4 colonnes), grand bloc contact avec formulaire, pied de page sur une ligne.
« Page de liens » passe en deux colonnes : carte d'identité à gauche, liens et encarts à droite.

**Structure commune aux deux mises en page** : en-tête propre à la personne (photo et nom
quand on a défilé, « Partager », « Contacter »), qui reste en haut de l'écran même dans un embed ;
bannière fondue dans le fond ; photo de profil ; chaque section dans un encart (carte d'un ton plus
clair que le fond) pour que rien ne touche les bords de l'embed ; pied de page CGV · Mentions
légales (identité du vendeur) · Confidentialité · Signaler · « Propulsé par kitly. ». Sur la page
publique (`profil.html`, `/kitly/p/<adresse>`), le fond est peint sur toute la page.
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
3. **Page Framer `/profile`** avec `profil.html` et des liens `kitly.fr/profile#<adresse>`
   (réglage par défaut des embeds et de `KITLY_PROFILE_URL_TEMPLATE`) : simple, une seule page
   Framer. Le hash n'est jamais envoyé au serveur : Google et les aperçus de partage ne voient
   que la page `/profile` (pas de photo ni de nom dans l'aperçu d'un lien collé).

Les embeds sont réglés par défaut sur l'option 3 (`/profile#`). Les anciens liens
`/profile?u=<adresse>` continuent de fonctionner (réécrits en `#<adresse>` à l'ouverture). L'option 1 est à
mettre en place avant le lancement (changer alors `PROFILE_URL` / `PUBLIC_BASE` et
`KITLY_PROFILE_URL_TEMPLATE`).

**Téléphone** : `builder.html`, `admin.html`, la marketplace et la vitrine vérifient au chargement (et à chaque redimensionnement) que leur
cadre Framer ne dépasse pas de la zone qui l'affiche ; sinon ils le ramènent à la largeur disponible. Réglage
conseillé dans Framer : composant Embed en largeur **Fill** (pas 100vw ni largeur fixe) sur le breakpoint mobile.

## Réglages (bloc CONFIG en haut de chaque script)

Les liens vers les pages du site sont **relatifs** (`/builder`, `/profile#…`) et
complétés automatiquement avec l'adresse du site qui affiche l'embed : les mêmes
fichiers fonctionnent sur `https://kitly.framer.website` et sur `https://kitly.fr`,
sans modification. Une URL complète (`https://…`) reste possible pour forcer un lien.

- `API_BASE` : `https://navire-app-api.onrender.com`
- `connexion.html` : `BUILDER_URL` (destination après connexion, sauf `?next=/chemin` interne), `CGU_URL`, `PRIVACY_URL`
- `subscribe.html` : `REGISTER_URL`, `BUILDER_URL`
- `profil.html` : `KITLY_URL`, `PRIVACY_URL` (citée par le formulaire de contact), `MARKETPLACE_URL`,
  `RESERVED` (chemins du site qui ne sont jamais des vitrines)
- `home.html` : `REGISTER_URL`, `LOGIN_URL`, `BUILDER_URL`, `PRICING_URL`, `PUBLIC_BASE`, `PROFILE_URL`
- `marketplace.html` : `MODE` (`full` / `showcase`), `PROFILE_URL` (lien d'une vitrine, `{slug}` remplacé :
  `https://kitly.fr/{slug}` avec l'option 1 ou 2 ci-dessus, `/profile#{slug}` avec l'option 3 ; `?src=marketplace` est
  inséré avant le hash),
  `MARKETPLACE_URL`, `REGISTER_URL`, `PAGE_SIZE`, `SHOWCASE_COUNT`
- `builder.html` : `LOGIN_URL`, `PRICING_URL`, `LEGAL_URL` (future page CGV vendeur),
  `PUBLIC_BASE` (vitrine publique = `PUBLIC_BASE` + slug), `ADMIN_URL` (retour du mode support)
- `admin.html` : `BUILDER_URL`, `PROFILE_URL` (`{slug}` remplacé)

Les domaines du site Kitly sont autorisés par `KITLY_CORS_ORIGINS` (propre à Kitly,
distinct de `CORS_ORIGINS` de NAVIRE) : kitly.fr, www.kitly.fr et kitly.framer.website par défaut.

## Messages envoyés à la page

`connexion.html` émet `{type: "KITLY_LOGGED_IN", token, user}` et
`{type: "KITLY_LOGGED_OUT"}` (postMessage vers la page), pour un futur badge de
compte dans le header.

## Templates de vitrine

Blanc, Noir, Cadre, Éditorial (toutes formules, couleurs fixes) ; Bold (fond
coloré à formes, gros titre, boutons pilule à icône — d'après l'inspiration) et
Studio, avec couleurs, typographie des titres et fond personnalisables, à partir
de Custom.
