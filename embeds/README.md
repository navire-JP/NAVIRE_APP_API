# Embeds — programme ambassadeur

| Fichier | Où | Rôle |
|---|---|---|
| `rejoindre.html` | page `/rejoindre?ref=CODE` | « Julie t'invite » → compte NAVIRE → matières → paiement Stripe |
| `ambassadeur.html` | page `/ambassadeur` | Présentation + connexion, candidature, attente, tableau de bord (lien, stats, filleuls, gains, simulateur) |
| `framer/NavireEmbed.tsx` | (repli) | Composant Framer qui affiche les deux pages servies par l'API |
| `framer/Navire_Ambassadeurs.tsx` | `/console` | Section « Ambassadeurs » importée par `Navire_AdminConsole.tsx` |

Charte : celle de NAVIRE AI (Inter + IBM Plex Mono, fond transparent,
panneaux blancs translucides, bouton principal blanc, états vert / jaune /
rouge `#5BE3A3` / `#FFD166` / `#FF6B6B`). La section console reprend les
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

1. Framer → Assets → Code → *New Code File* `Navire_Ambassadeurs`, coller
   `framer/Navire_Ambassadeurs.tsx`.
2. Dans `Navire_AdminConsole.tsx` :

```tsx
// en haut, sous `import * as React from "react"`
import { AmbassadeursSection } from "./Navire_Ambassadeurs.tsx"

// type Section : ajouter
    | "ambassadeurs"

// SECTIONS : ajouter après AdJuris
    { id: "ambassadeurs", label: "Amb." },

// ConsoleUI, à côté des autres sections
{section === "ambassadeurs" && <AmbassadeursSection />}

// ConsoleUI, barre d'onglets (div height: 42) : ajouter dans son style
    overflowX: "auto",
```

## Côté Stripe

Le webhook `/subscriptions/webhook` doit aussi recevoir `charge.refunded` et
`charge.dispute.created` (annulation de la commission d'un paiement remboursé
ou contesté).
