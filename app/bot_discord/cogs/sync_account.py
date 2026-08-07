# app/bot_discord/cogs/sync_account.py
"""
Liaison et synchronisation continue compte NAVIRE ↔ Discord.

Trois responsabilités :

1. `/setup_sync_embed` (admin) crée si besoin le salon
   « #🔹connecter-mon-compte-navire » et y poste un panneau sobre : un bouton
   bleu « Lier mes comptes » et un bouton « En savoir plus sur la connexion
   entre comptes » qui déplie les détails en éphémère.

2. À la liaison, le plan d'abonnement est lu et le rôle correspondant posé
   immédiatement (navire_ai, navire_ai+, prepadjuris — cf. PLAN_TO_ROLE).

3. Une boucle de surveillance relit périodiquement l'état des comptes liés
   (`GET /discord/sync-state`) et ne touche qu'aux membres dont l'abonnement a
   changé depuis le cycle précédent : rôle mis à jour et membre prévenu en MP.
   Couvre tous les chemins de changement de plan (Stripe, admin, expiration)
   sans avoir à instrumenter le code métier.
"""

from __future__ import annotations

import logging
import os

import discord
import httpx
from discord import app_commands
from discord.ext import commands, tasks

from app.bot_discord.config import (
    ADMIN_ROLE_ID,
    GUILD_ID,
    PLAN_TO_ROLE,
    SYNC_POLL_SECONDS,
)
from app.bot_discord.utils.api_client import get_sync_state, link_discord

logger = logging.getLogger(__name__)

SYNC_BUTTON_CUSTOM_ID = "sync_account:link"
INFO_BUTTON_CUSTOM_ID = "sync_account:info"

SYNC_CHANNEL_NAME = os.getenv(
    "DISCORD_SYNC_CHANNEL_NAME", "🔹connecter-mon-compte-navire"
)
SYNC_CHANNEL_TOPIC = "Lie ton compte navire-ai.com à ton compte Discord."
SITE_URL = os.getenv("NAVIRE_SITE_URL", "https://navire-ai.com")

# Libellé lisible des plans, pour les messages adressés aux membres.
PLAN_LABELS = {
    "free":    "Gratuit",
    "membre":  "NAVIRE AI",
    "membre+": "NAVIRE AI+",
    "prepa":   "Prép'AdJuris",
}


def _plan_label(plan: str) -> str:
    return PLAN_LABELS.get(plan, plan or "inconnu")


def _is_admin(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    role = discord.utils.get(member.guild.roles, id=ADMIN_ROLE_ID)
    return role in member.roles if role else False


# ── Modal de liaison ─────────────────────────────────────────────────────────


class SyncAccountModal(discord.ui.Modal, title="Lier mes comptes"):
    user_id = discord.ui.TextInput(
        label="Ton identifiant NAVIRE",
        placeholder="Ex. 1042 — visible dans ton profil sur navire-ai.com",
        max_length=16,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = self.user_id.value.strip().lstrip("#")

        if not raw.isdigit():
            await interaction.response.send_message(
                f"L'identifiant NAVIRE est un nombre (ex. `1042`). "
                f"Tu le trouves dans ton profil sur {SITE_URL}.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            result = await link_discord(int(raw), str(interaction.user.id))
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            if code == 404:
                message = (
                    f"Aucun compte NAVIRE avec l'identifiant `{raw}`. "
                    f"Vérifie-le dans ton profil sur {SITE_URL}."
                )
            elif code == 409:
                message = (
                    "Ce compte Discord est déjà lié à un autre compte NAVIRE. "
                    "Contacte un membre de l'équipe pour le délier."
                )
            else:
                message = "Le serveur NAVIRE n'a pas répondu correctement. Réessaie dans quelques instants."
            await interaction.followup.send(message, ephemeral=True)
            return
        except Exception:
            await interaction.followup.send(
                "Connexion au serveur NAVIRE impossible. Réessaie dans quelques instants.",
                ephemeral=True,
            )
            return

        if not (result or {}).get("ok"):
            await interaction.followup.send(
                "La liaison a échoué. Vérifie ton identifiant NAVIRE et réessaie.",
                ephemeral=True,
            )
            return

        # Applique tout de suite le rôle correspondant à l'abonnement.
        detail = ""
        cog = interaction.client.get_cog("SyncRolesCog")
        if cog and isinstance(interaction.user, discord.Member):
            try:
                plan = await cog.sync_member_role(interaction.user)
                if plan and plan != "not_linked":
                    detail = f"\nAbonnement détecté : **{_plan_label(plan)}**."
                    sync_cog = interaction.client.get_cog("SyncAccountCog")
                    if sync_cog:
                        sync_cog.remember_plan(str(interaction.user.id), plan)
            except Exception:
                logger.warning("Rôle non appliqué après liaison", exc_info=True)

        await interaction.followup.send(
            f"Comptes liés. Ton Discord est relié au compte NAVIRE `#{raw}`.{detail}\n"
            "Ton rôle suivra automatiquement les changements de ton abonnement.",
            ephemeral=True,
        )


# ── Vue persistante ──────────────────────────────────────────────────────────


def build_info_embed() -> discord.Embed:
    """Détail affiché en éphémère derrière « En savoir plus »."""
    roles = " · ".join(f"`{name}`" for name in PLAN_TO_ROLE.values())
    embed = discord.Embed(
        title="La connexion entre tes deux comptes",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="À quoi ça sert",
        value=(
            "Le serveur ne sait pas, par défaut, quel compte NAVIRE se cache "
            "derrière un pseudo Discord. La liaison fait ce rapprochement une "
            "fois pour toutes : le bot peut ensuite t'attribuer les accès qui "
            "correspondent à ton abonnement, et créditer ton activité ici sur "
            "ta progression côté plateforme."
        ),
        inline=False,
    )
    embed.add_field(
        name="Ce qui est synchronisé",
        value=(
            f"Ton abonnement, qui détermine ton rôle sur le serveur ({roles}) "
            "et les salons auxquels tu as accès. Le rôle est vérifié en continu : "
            "si ton abonnement change, démarre ou expire, ton rôle suit tout "
            "seul en quelques minutes, sans rien avoir à refaire.\n"
            "Ton activité sur le serveur alimente aussi ton ELO, ton streak et "
            "ta place au classement."
        ),
        inline=False,
    )
    embed.add_field(
        name="Ce qui est échangé",
        value=(
            "Uniquement le lien entre ton identifiant NAVIRE et ton identifiant "
            "Discord. Le bot ne lit pas tes messages privés, ne publie rien en "
            "ton nom, et n'a accès ni à ton mot de passe ni à tes moyens de "
            "paiement. Ta saisie et le résultat ne sont visibles que par toi."
        ),
        inline=False,
    )
    embed.add_field(
        name="Où trouver mon identifiant",
        value=(
            f"Connecte-toi sur {SITE_URL} et ouvre ton profil : l'identifiant "
            "est le nombre affiché à côté de ton nom d'utilisateur."
        ),
        inline=False,
    )
    embed.add_field(
        name="En cas de problème",
        value=(
            "« Déjà lié à un autre compte » signifie que ce Discord a été "
            "associé à un autre compte NAVIRE : un membre de l'équipe peut le "
            "délier. Si ton rôle n'apparaît pas après quelques minutes, "
            "utilise `//sync` pour forcer la mise à jour."
        ),
        inline=False,
    )
    return embed


class SyncAccountView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Lier mes comptes",
        style=discord.ButtonStyle.primary,  # bleu
        custom_id=SYNC_BUTTON_CUSTOM_ID,
    )
    async def link_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(SyncAccountModal())

    @discord.ui.button(
        label="En savoir plus sur la connexion entre comptes",
        style=discord.ButtonStyle.secondary,
        custom_id=INFO_BUTTON_CUSTOM_ID,
    )
    async def info_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_message(
            embed=build_info_embed(), ephemeral=True
        )


# ── Panneau ──────────────────────────────────────────────────────────────────


def build_welcome_embed() -> discord.Embed:
    return discord.Embed(
        title="Connecter mon compte NAVIRE",
        description=(
            "Relie ton compte navire-ai.com à ton compte Discord pour que le "
            "serveur te reconnaisse : tu reçois automatiquement le rôle et les "
            "salons correspondant à ton abonnement, et ton activité ici compte "
            "pour ta progression.\n\n"
            "**1.** Crée ton compte ou connecte-toi sur "
            f"{SITE_URL}\n"
            "**2.** Ouvre ton profil et copie ton identifiant (un nombre)\n"
            "**3.** Clique sur **Lier mes comptes** et colle-le\n\n"
            "L'opération prend dix secondes et ne se fait qu'une fois. "
            "Ta réponse n'est visible que par toi."
        ),
        color=discord.Color.blurple(),
    )


# ── Cog ──────────────────────────────────────────────────────────────────────


class SyncAccountCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # discord_id → dernier plan connu. Reconstruit au premier cycle après
        # un redémarrage, ce qui resynchronise au passage tout ce qui aurait
        # changé pendant que le bot était hors ligne.
        self._plans: dict[str, str] = {}
        self._seeded = False

    async def cog_load(self) -> None:
        self.watch_plans.start()

    def cog_unload(self) -> None:
        self.watch_plans.cancel()

    def remember_plan(self, discord_id: str, plan: str) -> None:
        """Évite une notification redondante juste après une liaison manuelle."""
        self._plans[discord_id] = plan

    # ── Surveillance des abonnements ─────────────────────────────────────────

    def _guilds(self) -> list[discord.Guild]:
        if GUILD_ID:
            guild = self.bot.get_guild(GUILD_ID)
            return [guild] if guild else []
        return list(self.bot.guilds)

    async def _apply(self, discord_id: str, plan: str, notify: bool) -> None:
        roles_cog = self.bot.get_cog("SyncRolesCog")
        if not roles_cog:
            return

        for guild in self._guilds():
            member = guild.get_member(int(discord_id))
            if member is None:
                continue
            try:
                await roles_cog.apply_plan_role(member, plan)
            except discord.HTTPException:
                logger.warning("Rôle non appliqué pour %s", discord_id, exc_info=True)
                continue

            if notify:
                await self._notify(member, plan)

    async def _notify(self, member: discord.Member, plan: str) -> None:
        role_name = PLAN_TO_ROLE.get(plan)
        if role_name:
            text = (
                f"Ton abonnement NAVIRE est passé à **{_plan_label(plan)}** : "
                f"le rôle `{role_name}` vient de t'être attribué sur le serveur."
            )
        else:
            text = (
                "Ton abonnement NAVIRE a pris fin : les rôles associés ont été "
                "retirés sur le serveur. Ton compte reste lié — un nouvel "
                "abonnement rétablira l'accès automatiquement."
            )
        try:
            await member.send(text)
        except (discord.Forbidden, discord.HTTPException):
            pass  # MP fermés : le rôle a été appliqué, c'est l'essentiel.

    @tasks.loop(seconds=SYNC_POLL_SECONDS)
    async def watch_plans(self) -> None:
        try:
            state = await get_sync_state()
        except Exception:
            logger.warning("Lecture de /discord/sync-state échouée", exc_info=True)
            return

        current = {
            row["discord_id"]: (row.get("plan") or "free")
            for row in state
            if row.get("discord_id")
        }

        # Abonnements modifiés, plus les comptes déliés côté API (disparus de
        # la réponse) qu'il faut ramener à "free" pour retirer leurs rôles.
        changed = {
            did: plan
            for did, plan in current.items()
            if self._plans.get(did) != plan
        }
        for did in self._plans:
            if did not in current:
                changed[did] = "free"

        for discord_id, plan in changed.items():
            await self._apply(discord_id, plan, notify=self._seeded)

        self._plans = current
        self._seeded = True

    @watch_plans.before_loop
    async def _before_watch(self) -> None:
        await self.bot.wait_until_ready()

    # ── Arrivée d'un membre déjà lié ─────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        plan = self._plans.get(str(member.id))
        if plan:
            await self._apply(str(member.id), plan, notify=False)

    # ── /setup_sync_embed ────────────────────────────────────────────────────

    async def _get_or_create_channel(
        self, guild: discord.Guild
    ) -> discord.TextChannel:
        """Retourne le salon de liaison, en le recréant s'il n'existe plus."""
        channel = discord.utils.get(guild.text_channels, name=SYNC_CHANNEL_NAME)
        if channel:
            return channel

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=True,
                read_message_history=True,
                send_messages=False,          # salon en lecture seule
                add_reactions=False,
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_messages=True,
                embed_links=True,
            ),
        }
        return await guild.create_text_channel(
            name=SYNC_CHANNEL_NAME,
            topic=SYNC_CHANNEL_TOPIC,
            overwrites=overwrites,
            reason="Salon de liaison des comptes NAVIRE",
        )

    async def _purge_old_panels(self, channel: discord.TextChannel) -> None:
        """Supprime les anciens panneaux du bot pour éviter les doublons."""
        try:
            async for message in channel.history(limit=50):
                if message.author.id == self.bot.user.id and message.embeds:
                    await message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass

    @app_commands.command(
        name="setup_sync_embed",
        description="Poste le panneau de liaison compte NAVIRE ↔ Discord (admin).",
    )
    @app_commands.guild_only()
    async def setup_sync_embed(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member) or not _is_admin(
            interaction.user
        ):
            await interaction.response.send_message(
                "Réservé aux admins.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            channel = await self._get_or_create_channel(interaction.guild)
        except discord.Forbidden:
            await interaction.followup.send(
                f"Il me manque la permission **Gérer les salons** pour créer "
                f"#{SYNC_CHANNEL_NAME}.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as e:
            await interaction.followup.send(
                f"Impossible de créer le salon : {e}", ephemeral=True
            )
            return

        await self._purge_old_panels(channel)

        try:
            await channel.send(embed=build_welcome_embed(), view=SyncAccountView())
        except discord.Forbidden:
            await interaction.followup.send(
                f"Je ne peux pas écrire dans {channel.mention}.", ephemeral=True
            )
            return

        await interaction.followup.send(
            f"Panneau de liaison publié dans {channel.mention}.", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(SyncAccountCog(bot))
