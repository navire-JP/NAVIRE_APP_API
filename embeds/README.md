# Embeds — programme ambassadeur

| Fichier | Où | Rôle |
|---|---|---|
| `rejoindre.html` | page `/rejoindre?ref=CODE` | « Julie t'invite » → compte NAVIRE → matières → paiement Stripe |
| `ambassadeur.html` | page `/ambassadeur` | Présentation + connexion, candidature, attente, tableau de bord (lien, stats, filleuls, gains, simulateur) |
| `framer/NavireEmbed.tsx` | `/rejoindre`, `/ambassadeur` | Composant Framer qui affiche les deux pages ci-dessus |
| `framer/Navire_Ambassadeurs.tsx` | `/console` | Section « Ambassadeurs » importée par `Navire_AdminConsole.tsx` |

Charte : celle de NAVIRE AI (Inter + IBM Plex Mono, fond transparent,
panneaux blancs translucides, bouton principal blanc, états vert / jaune /
rouge `#5BE3A3` / `#FFD166` / `#FF6B6B`). La section console reprend les
design tokens de la console.

## `/rejoindre` et `/ambassadeur`

Les pages sont servies par l'API (`GET /ambassadors/embed/{rejoindre|ambassadeur}`)
et affichées par le composant `NavireEmbed`, qui leur transmet le `?ref=CODE`
de l'adresse et ajuste la hauteur.

1. Framer → Assets → Code → *New Code File* `NavireEmbed`, coller `framer/NavireEmbed.tsx`.
2. Sur chaque page, glisser le composant, choisir la page dans le panneau.
   Largeur *Fill*, hauteur *Fit*.

Une modification de `rejoindre.html` / `ambassadeur.html` est en ligne dès le
redéploiement de l'API : rien à recoller dans Framer.

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
