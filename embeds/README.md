# Embeds — programme ambassadeur, NAVIRE Lib et professeurs

| Fichier | Où | Rôle |
|---|---|---|
| `rejoindre.html` | page `/rejoindre?ref=CODE` | « Julie t'invite » → compte NAVIRE → matières → paiement Stripe |
| `ambassadeur.html` | page `/ambassadeur` | Présentation + connexion, candidature, attente, tableau de bord (lien, stats, filleuls, gains, simulateur) |
| `profil.html` | page profil | Profil, classement, Discord, et paramètres du compte : abonnement (carte, prochain prélèvement, résiliation), factures, mot de passe |
| `lib.html` | page `/lib` | NAVIRE Lib : bibliothèque, lecture des cours avec plan, Lib (consultation) et Lib+ (une question par point de passage, chat NAVIRE AI) |
| `professeurs.html` | page `/professeurs` | Espace des chargés de TD : groupes (Discord, liens), étudiants, dépôt de PDF, rémunération prévisionnelle |
| `classement.html` | page `/classement` | Classement : podium, joueurs (général, 30 jours, 7 jours, ma fac), facs, fiche joueur avec ses stats, curseur « Mes stats visibles » |
| `framer/NavireEmbed.tsx` | (repli) | Composant Framer qui affiche les pages servies par l'API |
| `framer/Navire_AdminConsole.tsx` | `/console` | Console admin complète, avec l'onglet « Amb. » |
| `framer/Navire_Ambassadeurs.tsx` | `/console` | Section « Ambassadeurs » importée par la console |
| `framer/Navire_Lib.tsx` | `/console` | Section « Lib » importée par la console (import des cours) |
| `framer/Navire_Professeurs.tsx` | `/console` | Section « Prof. » importée par la console (professeurs, groupes, rémunération) |
| `framer/FlashcardsApp.tsx` | page Flashcards | Decks, cartes, révision, génération depuis un PDF ; charte bleu et blanc. Code File `FlashcardsApp`, le composant remplit son cadre |

Charte : celle de NAVIRE AI (Inter + IBM Plex Mono, fond transparent,
panneaux blancs translucides, bouton principal blanc, états vert / jaune /
rouge `#5BE3A3` / `#FFD166` / `#FF6B6B`), y compris dans la console, sauf
NAVIRE Lib : bleu et blanc.

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
   `Navire_AdminConsole`. Il importe la section ci-dessus et la section
   Lib ci-dessous, et ajoute les onglets « Amb. » et « Lib ». Charte NAVIRE AI
   (Inter, pastilles, couleurs d'état) ; l'onglet Carts (paniers MEOLES) est
   retiré.
3. `framer/Navire_Lib.tsx` → Code File nommé `Navire_Lib` (section « Lib »).
   Déjà branchée dans `Navire_AdminConsole.tsx` ; dans une copie plus
   ancienne de la console, quatre lignes l'activent :
   ```ts
   import { LibSection } from "./Navire_Lib.tsx"          // en tête
   | "lib"                                                // type Section
   { id: "lib", label: "Lib" },                           // SECTIONS
   {section === "lib" && <LibSection />}                  // rendu de ConsoleUI
   ```

4. `framer/Navire_Professeurs.tsx` → Code File nommé `Navire_Professeurs`
   (section « Prof. »), déjà importé par `Navire_AdminConsole`.

## `/professeurs` : espace des chargés de TD

**Embed HTML, comme l'ambassadeur.** Coller `professeurs.html` dans un Embed
HTML de la page `/professeurs` (servi aussi par l'API :
`GET /professeurs/embed`). Même charte que `ambassadeur.html`.

Côté console, onglet « Prof. » :

- **Groupes** : un groupe par matière est créé au premier démarrage, avec son
  salon Discord. On peut en ajouter d'autres (plusieurs groupes pour une même
  matière), renommer, changer le salon Discord, ajouter des liens (Drive,
  visio…), désactiver ou supprimer (refusé si le groupe a des documents). Un
  étudiant est dans le premier groupe actif de sa matière, sauf si on le
  change de groupe depuis la liste des étudiants du groupe.
- **Professeurs** : attacher un compte NAVIRE existant par son email, avec le
  grade « ChargéTD » (repris sur le compte), et un ou plusieurs groupes.
  Taux du prévisionnel par groupe : € par séance + € par étudiant et par
  séance (0 € par défaut, `PROF_REMU_SEANCE_CENTS`, `PROF_REMU_ETUDIANT_CENTS`).
  Suspendre coupe l'accès à l'espace ; détacher rend au compte le grade
  « Primo » (refusé dès qu'un mois a été versé : suspendre à la place).
- **Rémunérations** : tableau de pilotage de tous les professeurs, mois par
  mois et groupe par groupe : montant calculé, montant fixé (saisi à la main,
  il remplace le calcul ; vide = retour au calcul), montant retenu, note.
  **Rien ne passe par Stripe** : on fait le virement, puis on coche les lignes
  et « Marquer versé » (avec la référence du virement) fige le montant.
  « VERSÉ » annule un versement. Un mois sans séance peut aussi recevoir un
  montant (prime, rattrapage). Export CSV. Le professeur voit les montants
  retenus, et les mois « versé » ou « ajusté ».

Côté professeur : vue d'ensemble, groupes (bouton Discord, liens),
étudiants (recherche, filtre par groupe), documents et rémunération. Le
prévisionnel suit le calendrier des séances de la facturation (agenda de la
matière) : par séance, le montant fixe plus le montant par étudiant inscrit
avant la séance, mois par mois jusqu'à la fin du programme.

Un PDF déposé est publié tout de suite pour les étudiants du groupe : dans
leur espace Prép'AdJuris, et sur NAVIRE Lib via `GET /lib/adjuris` (section
« Prép'AdJuris », à ajouter à `lib.html`). Les autres groupes de la matière ne
le voient pas. Seul le professeur qui l'a déposé peut le retirer.

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
- **Mes fichiers** : les PDF que l'élève a déposés dans « Mes fichiers »
  apparaissent en tête de sa bibliothèque (lui seul les voit), dans Lib comme
  dans Lib+. Le PDF est lu à la première ouverture. En Lib+, des points de
  passage s'y placent tout seuls (parties du document, sinon tous les
  `LIB_PERSO_MOTS` mots, 1 200 par défaut) et l'IA rédige la question quand
  l'élève y arrive. Pas d'Elo sur ses propres fichiers. Fichier supprimé ou
  expiré : le document disparaît de Lib.
- **Pass admin** (comptes admin) : Lib+ ouvert sans abonnement, aucun verrou
  d'Elo, bouton « Passer (admin) » sur chaque point de passage dès la première
  question, et les brouillons s'ouvrent en aperçu (étiquette « Brouillon »).

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

## `/classement` : classement des joueurs

**Embed HTML, comme NAVIRE AI.** Coller `classement.html` dans un Embed HTML
de la page `/classement` (servi aussi par l'API : `GET /leaderboard/embed`).
Charte bleu et blanc, comme NAVIRE Lib.

- **Joueurs** : podium du top 3 puis la liste, à l'Elo (Général) ou à l'Elo
  gagné sur 30 ou 7 jours. Ex aequo au même rang. **Ma fac** filtre sur la fac
  du joueur connecté. Une barre « Ta place » reste en bas, avec l'écart avec la
  place du dessus.
- **Facs** : somme de l'Elo des étudiants de chaque fac, et la moyenne.
- **Fiche joueur** (clic sur un joueur) : Elo, place, palier, gains sur 7 et
  30 jours, séries, courbe d'Elo sur 30 jours, taux de réussite aux QCM,
  origine de l'Elo (QCM, Lib…), points de passage Lib.
- **Curseur « Mes stats visibles »** (activé par défaut) : masqué, les autres
  ne voient plus que le nom, l'Elo, la place et la fac du joueur, avec un
  cadenas dans la liste. Le joueur voit toujours ses propres stats.

Sans connexion, le classement reste visible, avec un bouton « Rejoindre ».

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

