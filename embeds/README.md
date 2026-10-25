# Embeds — programme ambassadeur et NAVIRE Lib

| Fichier | Où | Rôle |
|---|---|---|
| `rejoindre.html` | page `/rejoindre?ref=CODE` | « Julie t'invite » → compte NAVIRE → matières → paiement Stripe |
| `ambassadeur.html` | page `/ambassadeur` | Présentation + connexion, candidature, attente, tableau de bord (lien, stats, filleuls, gains, simulateur) |
| `lib.html` | page `/lib` | NAVIRE Lib : bibliothèque, lecture des cours avec plan, points de passage (QCM + questions à points), Elo |
| `framer/NavireEmbed.tsx` | (repli) | Composant Framer qui affiche les pages servies par l'API |
| `framer/Navire_AdminConsole.tsx` | `/console` | Console admin complète, avec l'onglet « Amb. » |
| `framer/Navire_Ambassadeurs.tsx` | `/console` | Section « Ambassadeurs » importée par la console |
| `framer/Navire_Lib.tsx` | `/console` | Section « Lib » importée par la console (import des cours) |

Charte : celle de NAVIRE AI (Inter + IBM Plex Mono, fond transparent,
panneaux blancs translucides, bouton principal blanc, états vert / jaune /
rouge `#5BE3A3` / `#FFD166` / `#FF6B6B`), y compris dans la console.

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

## `/lib` — NAVIRE Lib

**Embed HTML, comme NAVIRE AI.** Coller `lib.html` dans un Embed HTML de la
page `/lib`. Les liens des boutons QCM, NAVIRE AI et Actus sont dans le bloc
`CONFIG` du script. Un lien vers un cours précis : `/lib?doc=12` (si l'Embed
ne voit pas l'adresse de la page, utiliser `NavireEmbed` réglé sur « NAVIRE
Lib », qui la lui transmet).

Côté console, onglet « Lib » : importer un `.docx` (conseillé : gras,
italiques, tableaux et surlignage sont repris) ou un `.pdf`. Le serveur :

- découpe le cours en blocs (étapes, titres, chapitres, paragraphes numérotés
  « 3. Intitulé.- », textes cités, tableaux) et refait le plan ;
- surligne articles, décisions et textes (lois, règlements, directives) ;
- place un point de passage à chaque « POINTS DE PASSAGE » et tire les
  questions du tableau qui suit (sinon : fin de chaque grande partie) ;
- le k-ième point de passage demande k × palier Elo et en rapporte une
  récompense (réglables à l'import puis point par point).

Tant qu'un point de passage n'est pas validé (seuil 60 % par défaut), l'API
n'envoie pas la suite : l'embed n'en reçoit que quelques blocs, affichés
floutés sous la fenêtre du point de passage. Les questions s'éditent dans la
console, ou se génèrent avec l'IA (`OPENAI_MODEL_LIB`, sinon `OPENAI_MODEL`).
« Nouvelle version » remplace le fichier en gardant points de passage et
progression des élèves.

## Côté Stripe

Le webhook `/subscriptions/webhook` doit aussi recevoir `charge.refunded` et
`charge.dispute.created` (annulation de la commission d'un paiement remboursé
ou contesté).
