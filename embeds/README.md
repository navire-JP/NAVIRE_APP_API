# Embeds — programme ambassadeur

| Fichier | Page du site | Rôle |
|---|---|---|
| `rejoindre.html` | `/rejoindre?ref=CODE` | « Julie t'invite » → création du compte NAVIRE → choix des matières → paiement Stripe |
| `ambassadeur.html` | `/ambassadeur` | Présentation + connexion, candidature, attente, tableau de bord (lien, stats, filleuls, gains, simulateur) |
| `console_ambassadeurs.html` | `/console` | Onglet admin : candidatures, ambassadeurs, commissions à verser (export CSV, « marquer comme versées ») |
| `framer/NavireEmbed.tsx` | `/rejoindre`, `/ambassadeur` | Composant Framer qui affiche les deux premiers embeds |

## Installation dans Framer

**`/rejoindre` et `/ambassadeur` : utiliser le composant, pas un Embed HTML.**
Un Embed HTML Framer est une iframe cloisonnée (origine `null`) : il ne voit pas
le `?ref=CODE` de l'adresse et ne peut rien garder en mémoire. Le composant lit
le code dans la page et affiche la page servie par l'API
(`GET /ambassadors/embed/{rejoindre|ambassadeur}`).

1. Framer → Assets → Code → *New Code File* → nommer `NavireEmbed`, coller `framer/NavireEmbed.tsx`.
2. Créer les pages `/rejoindre` et `/ambassadeur`, y glisser le composant et
   choisir la page dans le panneau de droite. Largeur : *Fill* ; hauteur : *Fit*
   (l'embed transmet sa hauteur, pas de double barre de défilement).

Les modifications de `rejoindre.html` / `ambassadeur.html` sont en ligne dès
que l'API est redéployée : rien à recoller dans Framer.

**`/console` : Embed HTML classique.** Coller `console_ambassadeurs.html` dans
un nouvel onglet de la console. Il lit le jeton admin sous la clé
`navire_token` ; s'il ne le trouve pas, il affiche son propre formulaire de
connexion (compte `is_admin`).

## Réglages

En tête de chaque fichier, bloc `CONFIG` : URL de l'API, clé du jeton
(`navire_token`), URL d'inscription. Servis par l'API, les embeds appellent
l'API en relatif (l'URL du bloc est ignorée).

## Côté Stripe

Dans le dashboard Stripe, le webhook `/subscriptions/webhook` doit aussi
recevoir `charge.refunded` et `charge.dispute.created` : c'est ce qui annule
la commission d'un paiement remboursé ou contesté.
