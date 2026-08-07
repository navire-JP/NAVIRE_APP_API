# app/bot_discord/cogs/sync_account.py
"""
Panneau de liaison compte NAVIRE ↔ Discord.

`/setup_sync_embed` (admin) crée si besoin le salon
« #🔹connecter-mon-compte-navire », y purge l'ancien panneau du bot, puis poste
un embed d'accueil expliquant à quoi sert la synchronisation, avec un bouton
bleu persistant « Lier mes comptes ».

Le clic ouvre un modal demandant l'ID du compte NAVIRE (visible dans le profil
sur navire-ai.com) ; la soumission appelle `POST /discord/link` puis
resynchronise immédiatement le rôle Discord du membre selon son plan.
"""

from __future__ import annotations

import os

import discord
import httpx
from discord import app_commands
from discord.ext import commands

from app.bot_discord.config import ADMIN_ROLE_ID
from app.bot_discord.utils.api_client import link_discord

SYNC_BUTTON_CUSTOM_ID = "sync_account:link"

SYNC_CHANNEL_NAME = os.getenv(
    "DISCORD_SYNC_CHANNEL_NAME", "🔹connecter-mon-compte-navire"
)
SYNC_CHANNEL_TOPIC = "Lie ton compte navire-ai.com à ton compte Discord."
SITE_URL = os.getenv("NAVIRE_SITE_URL", "https://navire-ai.com")


def _is_admin(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    role = discord.utils.get(member.guild.roles, id=ADMIN_ROLE_ID)
    return role in member.roles if role else False


# ── Modal ────────────────────────────────────────────────────────────────────


class SyncAccountModal(discord.ui.Modal, title="Lier mes comptes NAVIRE"):
    user_id = discord.ui.TextInput(
        label="Ton ID de compte NAVIRE",
        placeholder="Ex. 1042 — visible dans ton profil sur navire-ai.com",
        max_length=16,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = self.user_id.value.strip().lstrip("#")

        if not raw.isdigit():
            await interaction.response.send_message(
                "❌ L'ID NAVIRE doit être un nombre (ex. `1042`).\n"
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
                    f"❌ Aucun compte NAVIRE avec l'ID `{raw}`. "
                    f"Vérifie l'ID dans ton profil sur {SITE_URL}."
                )
            elif code == 409:
                message = (
                    "❌ Ce compte Discord est déjà lié à un autre compte NAVIRE. "
                    "Contacte un administrateur."
                )
            else:
                message = "❌ Erreur du serveur NAVIRE. Réessaie dans quelques instants."
            await interaction.followup.send(message, ephemeral=True)
            return
        except Exception:
            await interaction.followup.send(
                "❌ Erreur de connexion au serveur NAVIRE. Réessaie dans quelques instants.",
                ephemeral=True,
            )
            return

        if not (result or {}).get("ok"):
            await interaction.followup.send(
                "❌ Liaison échouée. Vérifie ton ID NAVIRE et réessaie.",
                ephemeral=True,
            )
            return

        # Applique tout de suite le rôle correspondant au plan NAVIRE.
        detail = ""
        cog = interaction.client.get_cog("SyncRolesCog")
        if cog and isinstance(interaction.user, discord.Member):
            try:
                plan = await cog.sync_member_role(interaction.user)
                if plan and plan != "not_linked":
                    detail = f"\nPlan détecté : **{plan}** — ton rôle a été mis à jour."
            except Exception:
                pass

        await interaction.followup.send(
            f"✅ Comptes liés ! Ton Discord est désormais relié au compte NAVIRE "
            f"`#{raw}`.{detail}",
            ephemeral=True,
        )


# ── Vue persistante ──────────────────────────────────────────────────────────


class SyncAccountView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Lier mes comptes",
        emoji="🔗",
        style=discord.ButtonStyle.primary,  # bleu
        custom_id=SYNC_BUTTON_CUSTOM_ID,
    )
    async def link_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(SyncAccountModal())


# ── Embed ────────────────────────────────────────────────────────────────────


def build_welcome_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🔹 Connecte ton compte NAVIRE à Discord",
        description=(
            "Bienvenue ! Ce salon sert à **relier ton compte navire-ai.com à ton "
            "compte Discord**.\n\n"
            "Une fois les deux comptes liés, le serveur te reconnaît "
            "automatiquement : tu récupères les rôles correspondant à ton "
            "abonnement, et ton activité ici compte pour ta progression sur la "
            "plateforme."
        ),
        color=discord.Color.blurple(),
    )

    embed.add_field(
        name="🎁 Ce que ça te débloque",
        value=(
            "• Les **salons réservés** à ton abonnement (NAVIRE AI / AI+)\n"
            "• Le gain d'**ELO** quand tu participes sur le serveur\n"
            "• Ton **streak quotidien** et ta place au **classement**\n"
            "• Un rôle mis à jour tout seul quand ton abonnement change"
        ),
        inline=False,
    )

    embed.add_field(
        name="1️⃣ Crée ton compte NAVIRE",
        value=(
            f"Rends-toi sur **{SITE_URL}** et crée ton compte "
            "(ou connecte-toi si tu en as déjà un)."
        ),
        inline=False,
    )
    embed.add_field(
        name="2️⃣ Récupère ton ID NAVIRE",
        value=(
            "Ouvre ton **profil** sur le site : ton identifiant s'y affiche "
            "(un nombre, ex. `1042`). Copie-le."
        ),
        inline=False,
    )
    embed.add_field(
        name="3️⃣ Clique sur « Lier mes comptes »",
        value=(
            "Le bouton bleu ci-dessous ouvre une petite fenêtre : colle ton ID "
            "NAVIRE, valide, et c'est terminé."
        ),
        inline=False,
    )

    embed.add_field(
        name="🔒 Confidentialité",
        value=(
            "Ta réponse n'est visible que par toi : personne sur le serveur ne "
            "voit ton ID ni le résultat de la liaison."
        ),
        inline=False,
    )

    embed.set_footer(text="Un souci ? Contacte un membre de l'équipe NAVIRE.")
    return embed


# ── Cog ──────────────────────────────────────────────────────────────────────


class SyncAccountCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

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
                "❌ Réservé aux admins.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            channel = await self._get_or_create_channel(interaction.guild)
        except discord.Forbidden:
            await interaction.followup.send(
                f"❌ Il me manque la permission **Gérer les salons** pour créer "
                f"#{SYNC_CHANNEL_NAME}.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as e:
            await interaction.followup.send(
                f"❌ Impossible de créer le salon : {e}", ephemeral=True
            )
            return

        await self._purge_old_panels(channel)

        try:
            await channel.send(embed=build_welcome_embed(), view=SyncAccountView())
        except discord.Forbidden:
            await interaction.followup.send(
                f"❌ Je ne peux pas écrire dans {channel.mention}.", ephemeral=True
            )
            return

        await interaction.followup.send(
            f"✅ Panneau de liaison publié dans {channel.mention}.", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(SyncAccountCog(bot))
