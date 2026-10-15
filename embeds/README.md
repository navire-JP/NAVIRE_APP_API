# Embeds — programme ambassadeur

| Fichier | Où | Rôle |
|---|---|---|
| `rejoindre.html` | page `/rejoindre?ref=CODE` | « Julie t'invite » → compte NAVIRE → matières → paiement Stripe |
| `ambassadeur.html` | page `/ambassadeur` | Présentation + connexion, candidature, attente, tableau de bord (lien, stats, filleuls, gains, simulateur) |
| `framer/NavireEmbed.tsx` | (repli) | Composant Framer qui affiche les deux pages servies par l'API |
| `framer/Navire_AdminConsole.tsx` | `/console` | Console admin complète, avec l'onglet « Amb. » |
| `framer/Navire_Ambassadeurs.tsx` | `/console` | Section « Ambassadeurs » importée par la console |

Charte : celle de NAVIRE AI (Inter + IBM Plex Mono, fond transparent,
panneaux blancs translucides, bouton principal blanc, états vert / jaune /
rouge `#5BE3A3` / `#FFD166` / `#FF6B6B`), y compris dans la console.

## `/rejoindre` et `/ambassadeur`

**Embed HTML, comme NAVIRE AI.** Coller `rejoindre.html` dans un Embed HTML
de la page `/rejoindre`, et `ambassadeur.html` dans celui de `/ambassadeur`.
L'embed lit le `?ref=CODE` de la page, reprend la connexion du site
(`navire_token`) et agrandit lui-même son iframe à la hauteur du contenu :
pas de défilement interne, c'est la page du site qui défile.

Si un jour l'embed ne voit plus le `?ref=` (iframe cloisonnée), le composant
`framer/NavireEmbed.tsx` sert de solution de repli : il affiche les mêmes
pages servies par l'API (`GET /ambassadors/embed/{rejoindre|ambassadeur}`).

## `/console`

Deux Code Files Framer, à coller tels quels :

1. `framer/Navire_Ambassadeurs.tsx` → Code File nommé `Navire_Ambassadeurs`
   (section « Ambassadeurs »).
2. `framer/Navire_AdminConsole.tsx` → remplace le Code File existant
   `Navire_AdminConsole`. Il importe la section ci-dessus et ajoute l'onglet
   « Amb. ». Charte NAVIRE AI (Inter, pastilles, couleurs d'état) ; l'onglet
   Carts (paniers MEOLES) est retiré.

## Côté Stripe

Le webhook `/subscriptions/webhook` doit aussi recevoir `charge.refunded` et
`charge.dispute.created` (annulation de la commission d'un paiement remboursé
ou contesté).
