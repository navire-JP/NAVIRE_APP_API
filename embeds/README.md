# Embeds — programme ambassadeur et NAVIRE Lib

| Fichier | Où | Rôle |
|---|---|---|
| `rejoindre.html` | page `/rejoindre?ref=CODE` | « Julie t'invite » → compte NAVIRE → matières → paiement Stripe |
| `ambassadeur.html` | page `/ambassadeur` | Présentation + connexion, candidature, attente, tableau de bord (lien, stats, filleuls, gains, simulateur) |
| `profil.html` | page profil | Profil, classement, Discord, et paramètres du compte : abonnement (carte, prochain prélèvement, résiliation), factures, mot de passe |
| `lib.html` | page `/lib` | NAVIRE Lib : bibliothèque, lecture des cours avec plan, Lib (consultation) et Lib+ (une question par point de passage, chat NAVIRE AI) |
| `framer/NavireEmbed.tsx` | (repli) | Composant Framer qui affiche les pages servies par l'API |
| `framer/Navire_AdminConsole.tsx` | `/console` | Console admin complète, avec l'onglet « Amb. » |
| `framer/Navire_Ambassadeurs.tsx` | `/console` | Section « Ambassadeurs » importée par la console |
| `framer/Navire_Lib.tsx` | `/console` | Section « Lib » importée par la console (import des cours) |

Charte : celle de NAVIRE AI (Inter + IBM Plex Mono, fond transparent,
panneaux blancs translucides, bouton principal blanc, états vert / jaune /
rouge `#5BE3A3` / `#FFD166` / `#FF6B6B`), sauf NAVIRE Lib : bleu et blanc. La section console reprend les
design tokens de la console.

## `/rejoindre` et `/ambassadeur`

**Embed HTML, comme NAVIRE AI.** Coller `rejoindre.html` dans un Embed HTML
de la page `/rejoindre`, et `ambassadeur.html` dans celui de `/ambassadeur`.
L'embed lit le `?ref=CODE` de la page, reprend la connexion du site
(`navire_token`) et agrandit lui-même son iframe à la hauteur du contenu.

Si un jour l'embed ne voit plus le `?ref=` (iframe cloisonnée), le composant
`framer/NavireEmbed.tsx` sert de solution de repli : il affiche les mêmes
pages servies par l'API (`GET /ambassadors/embed/{rejoindre|ambassadeur}`).

## `/console`

Deux Code Files Framer, à coller tels quels :

1. `framer/Navire_Ambassadeurs.tsx` → Code File nommé `Navire_Ambassadeurs`
   (section « Ambassadeurs »).
2. `framer/Navire_AdminConsole.tsx` → remplace le Code File existant
   `Navire_AdminConsole`. Il importe la section ci-dessus, ajoute l'onglet
   « Amb. » et rend la barre d'onglets défilante (8 onglets dans 583 px).
3. `framer/Navire_Lib.tsx` → Code File nommé `Navire_Lib` (section « Lib »).
   Dans `Navire_AdminConsole`, quatre lignes l'activent :
   ```ts
   import { LibSection } from "./Navire_Lib.tsx"          // en tête
   | "lib"                                                // type Section
   { id: "lib", label: "Lib" },                           // SECTIONS
   {section === "lib" && <LibSection />}                  // rendu de ConsoleUI
   ```

## `/lib` : NAVIRE Lib et Lib+

**Embed HTML, comme NAVIRE AI.** Coller `lib.html` dans un Embed HTML de la
page `/lib`. Les liens (QCM, NAVIRE AI, Actus, offres) sont dans le bloc
`CONFIG` du script. Un lien vers un cours précis : `/lib?doc=12`, avec
`&mode=lib` pour l'ouvrir dans Lib (si l'Embed ne voit pas l'adresse de la
page, utiliser `NavireEmbed` réglé sur « NAVIRE Lib », qui la lui transmet).

Charte : bleu et blanc uniquement (pas de violet ni de jaune).

Un curseur en haut à droite passe de l'un à l'autre :

- **Lib** (tout compte) : consultation. Le cours entier, sans point de
  passage ni Elo. Les documents réservés à Lib+ n'y figurent pas.
- **Lib+** (abonnés NAVIRE AI, NAVIRE AI+, ou inscrits AdJuris actifs ; les
  comptes gratuits voient la page d'offre) : tous les documents, et les points
  de passage intégrés au cours, sans fenêtre. Chaque point de passage pose
  **une seule question** ; tant qu'elle n'est pas réussie, la suite reste
  floutée. Un nouvel essai tire une autre question de la réserve du point de
  passage. Après **3 échecs** (`LIB_ESSAIS_MAX`), l'élève peut passer à la
  suite, sans gagner d'Elo. Une fois le point de passage franchi, un chat
  NAVIRE AI s'ouvre dessous : il répond à partir de l'extrait lu
  (`POST /lib/chat`, même quota quotidien que NAVIRE AI).

Côté console, onglet « Lib » : importer un `.docx` (conseillé : gras,
italiques, tableaux et surlignage sont repris) ou un `.pdf`, et cocher
« Réservé à Lib+ » pour les documents en plus (modifiable ensuite avec
« Réserver à Lib+ » / « Ouvrir à Lib »). Le serveur :

- découpe le cours en blocs (étapes, titres, chapitres, paragraphes numérotés
  « 3. Intitulé.- », textes cités, tableaux) et refait le plan ;
- surligne articles, décisions et textes (lois, règlements, directives) ;
- place un point de passage à chaque « POINTS DE PASSAGE » et tire la réserve
  de questions du tableau qui suit (sinon : fin de chaque grande partie) ;
- le k-ième point de passage demande k × palier Elo et en rapporte une
  récompense (réglables à l'import puis point par point).

Tant qu'un point de passage n'est pas franchi, l'API n'envoie pas la suite :
l'embed n'en reçoit que quelques blocs, affichés floutés sous la question. Les
questions s'éditent dans la console, ou se génèrent avec l'IA
(`OPENAI_MODEL_LIB`, sinon `OPENAI_MODEL`). « Nouvelle version » remplace le
fichier en gardant points de passage et progression des élèves.

## Côté Stripe

Le webhook `/subscriptions/webhook` doit aussi recevoir `charge.refunded` et
`charge.dispute.created` (annulation de la commission d'un paiement remboursé
ou contesté).

## Page profil — paramètres du compte

**Embed HTML.** Coller `profil.html` dans l'Embed HTML de la page profil
(remplace la version précédente). Le lien « Voir les offres » est dans le bloc
`CONFIG` du script.

La roue ouvre trois onglets :

- **Abonnement** : NAVIRE AI et Prép'AdJuris, avec l'état, la carte, le
  prochain prélèvement, et les actions (changer de carte, résilier, réactiver,
  changer de formule). Source : `GET /subscriptions/billing`.
- **Factures** : les 12 dernières, avec le PDF et le lien de règlement.
- **Sécurité** : adresse, méthode de connexion, mot de passe.

« Changer de carte » ouvre une page Stripe (`POST /subscriptions/payment-method`)
où l'élève enregistre sa carte sans payer. Stripe le ramène sur la page profil
avec `?billing=card_updated`, et l'onglet Abonnement se rouvre. L'adresse de
retour est celle de la page (si elle est sur le domaine du site), sinon
`STRIPE_ACCOUNT_RETURN_URL` (par défaut `{FRONTEND_URL}/profil`).

En cas d'impayé, une alerte rouge s'affiche sous l'en-tête du profil et mène
directement à l'onglet Abonnement.

