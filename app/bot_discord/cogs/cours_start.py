# app/bot_discord/cogs/cours_start.py
"""
Salon #cours-start : les professeurs lancent leur cours depuis Discord.

- Un message permanent (embed blanc, bouton « Lancer un cours ») est posté
  dans le salon. Il ne change jamais : tout le reste se passe dans des
  réponses éphémères, visibles du seul professeur.
- Lancer un cours → choix du groupe (liste déroulante) → cours commencé :
  l'heure est décomptée par un horodatage Discord relatif, qui se met à jour
  tout seul (aucune édition de message nécessaire).
- Au-delà de l'heure, le cours est en extra. Après 10 minutes d'extra, le
  bouton « Ajouter un extra » liste les étudiants du groupe (nom, et pseudo
  Discord si le compte est lié) ; le professeur retire ceux qui partent.
- « Terminer le cours » : avec un extra, le cours compte 1 h 30 et chaque
  étudiant resté est facturé 10 € (règles : app/services/cours_live.py).

Les boutons des réponses éphémères sont des DynamicItem : leur custom_id
porte l'identifiant du cours, ils fonctionnent donc après un redémarrage du
bot et au-delà des 15 minutes de vie d'un jeton d'interaction (chaque clic
est une nouvelle interaction).
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from app.bot_discord.config import ADMIN_ROLE_ID, COLOR_WHITE, COURS_START_CHANNEL_ID
from app.bot_discord.utils.api_client import (
    cours_action,
    cours_professeur,
    cours_start,
    cours_toggle_eleve,
)

logger = logging.getLogger(__name__)

LANCER_CUSTOM_ID = "cours_start:lancer"
MAX_BOUTONS_ELEVES = 20  # 4 rangées de 5 ; la 5e rangée porte les actions

_ERREUR_API = "Connexion au serveur NAVIRE impossible. Réessaie dans quelques instants."


def _is_admin(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    role = discord.utils.get(member.guild.roles, id=ADMIN_ROLE_ID)
    return role in member.roles if role else False


def _euros(cents: int) -> str:
    return f"{cents / 100:.2f}".replace(".00", "").replace(".", ",") + " €"


def _duree(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{h} h {m:02d}" if m else f"{h} h"


# ── Embeds ──────────────────────────────────────────────────────────────────


def build_panel_embed() -> discord.Embed:
    return discord.Embed(
        title="Lancer un cours",
        description=(
            "Réservé aux professeurs Prép'AdJuris.\n\n"
            "Lance ton cours au moment où il commence : l'heure de cours est "
            "décomptée à partir de ce moment. Si le cours se prolonge de plus "
            "de 10 minutes, tu pourras ajouter un extra et indiquer les élèves "
            "qui restent.\n\n"
            "Tout se passe dans des messages visibles de toi seul."
        ),
        color=COLOR_WHITE,
    )


def _pseudo(guild: discord.Guild | None, discord_id: str | None) -> str:
    if not discord_id:
        return ""
    member = guild.get_member(int(discord_id)) if guild else None
    return f"@{member.display_name}" if member else f"<@{discord_id}>"


def _ligne_eleve(guild: discord.Guild | None, e: dict) -> str:
    pseudo = _pseudo(guild, e.get("discord_id"))
    texte = e["nom"] + (f" ({pseudo})" if pseudo else "")
    return f"~~{texte}~~ — retiré" if e["retire"] else texte


def _liste_eleves(guild: discord.Guild | None, c: dict) -> str:
    if not c["eleves"]:
        return "Aucun élève inscrit dans ce groupe."
    lignes = [f"{i}. {_ligne_eleve(guild, e)}" for i, e in enumerate(c["eleves"], 1)]
    texte = "\n".join(lignes)
    return texte if len(texte) <= 1024 else texte[:1000] + "\n…"


def build_choix_embed() -> discord.Embed:
    return discord.Embed(
        title="Lancer un cours",
        description="Choisis la matière du cours. L'heure démarre dès ton choix.",
        color=COLOR_WHITE,
    )


def build_cours_embed(c: dict, guild: discord.Guild | None, confirmer: bool = False) -> discord.Embed:
    debut, fin_h, extra_ok = c["started_ts"], c["fin_heure_ts"], c["extra_possible_ts"]
    prix = _euros(c["extra_prix_cents"])
    phase = c["phase"]

    if phase == "termine":
        facturés = [e for e in c["eleves"] if not e["retire"]]
        desc = (
            f"**{c['groupe']}**\n"
            f"Début : <t:{debut}:t> · Fin : <t:{c['ended_ts']}:t>\n"
            f"Durée comptée : **{_duree(c['duree_comptee_minutes'])}**"
        )
        if c["extra_ts"]:
            desc += f"\n\nExtra facturé {prix} à {len(facturés)} élève(s)."
        embed = discord.Embed(title="Cours terminé", description=desc, color=COLOR_WHITE)
        if c["extra_ts"] and c["eleves"]:
            embed.add_field(name="Élèves", value=_liste_eleves(guild, c), inline=False)
        return embed

    desc = f"**{c['groupe']}**\nDébut : <t:{debut}:t> (<t:{debut}:R>)\n"
    if phase == "heure":
        desc += (
            f"Fin de l'heure : <t:{fin_h}:t> (<t:{fin_h}:R>)\n\n"
            f"Si le cours se prolonge, l'extra pourra être ajouté à partir de <t:{extra_ok}:t>."
        )
    elif phase == "extra":
        desc += (
            f"Heure terminée <t:{fin_h}:R> : le cours est en extra.\n\n"
            f"L'extra pourra être ajouté <t:{extra_ok}:R> (après 10 minutes de dépassement)."
        )
    elif phase == "extra_disponible":
        desc += (
            f"Heure terminée <t:{fin_h}:R> : le cours est en extra depuis plus de 10 minutes.\n\n"
            f"Tu peux ajouter l'extra : le cours comptera 1 h 30 et chaque élève "
            f"resté sera facturé {prix}."
        )
    else:  # extra_ajoute
        desc += (
            f"Extra ajouté <t:{c['extra_ts']}:R> : le cours compte 1 h 30.\n\n"
            f"Retire les élèves qui quittent le cours. Les autres seront facturés "
            f"{prix} à la fin du cours."
        )

    embed = discord.Embed(title="Cours commencé", description=desc, color=COLOR_WHITE)
    if phase == "extra_ajoute":
        embed.add_field(
            name=f"Élèves de l'extra — {c['nb_restants']} restant(s)",
            value=_liste_eleves(guild, c),
            inline=False,
        )
    if confirmer:
        if c["extra_ts"]:
            msg = f"{c['nb_restants']} élève(s) seront facturés {prix} et le cours comptera 1 h 30."
        else:
            msg = "Le cours comptera 1 h. Aucun extra ne sera facturé."
        embed.add_field(name="Terminer le cours ?", value=msg, inline=False)
    return embed


# ── Boutons dynamiques (persistants, identifiant du cours dans le custom_id) ──


async def _repondre(interaction: discord.Interaction, data: dict | None, confirmer: bool = False) -> None:
    """Met à jour la réponse éphémère, ou signale le refus de l'API."""
    if data is None:
        await interaction.followup.send(_ERREUR_API, ephemeral=True)
        return
    if not data.get("ok"):
        await interaction.followup.send(data.get("message") or _ERREUR_API, ephemeral=True)
        return
    c = data["cours"]
    await interaction.edit_original_response(
        embed=build_cours_embed(c, interaction.guild, confirmer=confirmer),
        view=build_cours_view(c, confirmer=confirmer),
    )


class CoursBouton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"cours:(?P<action>state|extra|end|endok):(?P<id>\d+)",
):
    def __init__(
        self,
        action: str,
        cours_id: int,
        label: str = "Actualiser",
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        row: int | None = None,
    ) -> None:
        super().__init__(discord.ui.Button(
            label=label, style=style, row=row, custom_id=f"cours:{action}:{cours_id}",
        ))
        self.action = action
        self.cours_id = cours_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["action"], int(match["id"]), label=item.label, style=item.style)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        discord_id = str(interaction.user.id)
        # « end » ne fait que demander confirmation ; « endok » termine.
        api_action = {"state": "state", "extra": "extra", "end": "state", "endok": "end"}[self.action]
        try:
            data = await cours_action(discord_id, self.cours_id, api_action)
        except Exception:
            logger.exception("cours %s : action %s", self.cours_id, self.action)
            data = None
        await _repondre(interaction, data, confirmer=self.action == "end")


class EleveBouton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"cours:eleve:(?P<id>\d+):(?P<eleve>\d+)",
):
    def __init__(
        self,
        cours_id: int,
        eleve_id: int,
        label: str = "Élève",
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        row: int | None = None,
    ) -> None:
        super().__init__(discord.ui.Button(
            label=label, style=style, row=row, custom_id=f"cours:eleve:{cours_id}:{eleve_id}",
        ))
        self.cours_id = cours_id
        self.eleve_id = eleve_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["id"]), int(match["eleve"]), label=item.label, style=item.style)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        try:
            data = await cours_toggle_eleve(str(interaction.user.id), self.cours_id, self.eleve_id)
        except Exception:
            logger.exception("cours %s : bascule élève %s", self.cours_id, self.eleve_id)
            data = None
        await _repondre(interaction, data)


def _label(prefixe: str, nom: str) -> str:
    texte = f"{prefixe} {nom}"
    return texte if len(texte) <= 80 else texte[:79] + "…"


def build_cours_view(c: dict, confirmer: bool = False) -> discord.ui.View | None:
    if c["phase"] == "termine":
        return None
    view = discord.ui.View(timeout=None)
    cid = c["id"]

    if confirmer:
        view.add_item(CoursBouton("endok", cid, "Confirmer la fin du cours", discord.ButtonStyle.danger))
        view.add_item(CoursBouton("state", cid, "Retour"))
        return view

    if c["phase"] == "extra_ajoute":
        for i, e in enumerate(c["eleves"][:MAX_BOUTONS_ELEVES]):
            if e["retire"]:
                view.add_item(EleveBouton(cid, e["id"], _label("Remettre", e["nom"]),
                                          discord.ButtonStyle.secondary, row=i // 5))
            else:
                view.add_item(EleveBouton(cid, e["id"], _label("Retirer", e["nom"]),
                                          discord.ButtonStyle.danger, row=i // 5))
    else:
        view.add_item(CoursBouton("extra", cid, "Ajouter un extra", discord.ButtonStyle.primary, row=4))

    view.add_item(CoursBouton("state", cid, "Actualiser", row=4))
    view.add_item(CoursBouton("end", cid, "Terminer le cours", discord.ButtonStyle.secondary, row=4))
    return view


# ── Choix de la matière ─────────────────────────────────────────────────────


class ChoixGroupeSelect(discord.ui.Select):
    def __init__(self, groupes: list[dict]) -> None:
        super().__init__(
            placeholder="Choisir la matière",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label=g["label"][:100], value=str(g["classe_id"]))
                for g in groupes[:25]
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        try:
            data = await cours_start(str(interaction.user.id), int(self.values[0]))
        except Exception:
            logger.exception("cours_start")
            data = None
        await _repondre(interaction, data)


class ChoixGroupeView(discord.ui.View):
    def __init__(self, groupes: list[dict]) -> None:
        super().__init__(timeout=600)
        self.add_item(ChoixGroupeSelect(groupes))


# ── Panneau permanent ───────────────────────────────────────────────────────


class LancerCoursView(discord.ui.View):
    """Vue persistante du message permanent (custom_id fixe)."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Lancer un cours", style=discord.ButtonStyle.primary, custom_id=LANCER_CUSTOM_ID)
    async def lancer(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            data = await cours_professeur(str(interaction.user.id))
        except Exception:
            logger.exception("cours_professeur")
            await interaction.followup.send(_ERREUR_API, ephemeral=True)
            return

        if not data or not data.get("ok"):
            await interaction.followup.send((data or {}).get("message") or _ERREUR_API, ephemeral=True)
            return

        # Un cours déjà lancé : on le rouvre plutôt que d'en créer un second.
        if data.get("cours"):
            c = data["cours"]
            await interaction.followup.send(
                embed=build_cours_embed(c, interaction.guild),
                view=build_cours_view(c) or discord.utils.MISSING,
                ephemeral=True,
            )
            return

        groupes = data.get("groupes") or []
        if not groupes:
            await interaction.followup.send(
                "Aucun groupe ne t'est attribué pour le moment. Contacte un administrateur.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            embed=build_choix_embed(), view=ChoixGroupeView(groupes), ephemeral=True
        )


def _est_le_panneau(message: discord.Message, bot_id: int) -> bool:
    if message.author.id != bot_id:
        return False
    return any(
        getattr(child, "custom_id", None) == LANCER_CUSTOM_ID
        for row in message.components
        for child in getattr(row, "children", [])
    )


class CoursStartCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._panneau_verifie = False

    async def _salon(self) -> discord.TextChannel | None:
        if not COURS_START_CHANNEL_ID:
            return None
        channel = self.bot.get_channel(COURS_START_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(COURS_START_CHANNEL_ID)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return None
        return channel if isinstance(channel, discord.TextChannel) else None

    async def _panneaux(self, channel: discord.TextChannel) -> list[discord.Message]:
        try:
            return [m async for m in channel.history(limit=50) if _est_le_panneau(m, self.bot.user.id)]
        except (discord.Forbidden, discord.HTTPException):
            return []

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Poste le message permanent s'il n'est pas déjà dans le salon. Un
        message existant n'est jamais modifié."""
        if self._panneau_verifie:
            return
        self._panneau_verifie = True
        channel = await self._salon()
        if channel is None:
            logger.warning("Salon #cours-start (%s) introuvable", COURS_START_CHANNEL_ID)
            return
        if await self._panneaux(channel):
            return
        try:
            await channel.send(embed=build_panel_embed(), view=LancerCoursView())
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.error("Panneau #cours-start non posté : %s", e)

    @app_commands.command(
        name="setup_cours_embed",
        description="Reposte le message permanent de #cours-start (admin).",
    )
    @app_commands.guild_only()
    async def setup_cours_embed(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member) or not _is_admin(interaction.user):
            await interaction.response.send_message("Réservé aux admins.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        channel = await self._salon()
        if channel is None:
            await interaction.followup.send("Salon #cours-start introuvable.", ephemeral=True)
            return
        for m in await self._panneaux(channel):
            try:
                await m.delete()
            except (discord.Forbidden, discord.HTTPException):
                pass
        try:
            await channel.send(embed=build_panel_embed(), view=LancerCoursView())
        except discord.Forbidden:
            await interaction.followup.send(f"Je ne peux pas écrire dans {channel.mention}.", ephemeral=True)
            return
        await interaction.followup.send(f"Message permanent publié dans {channel.mention}.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CoursStartCog(bot))
