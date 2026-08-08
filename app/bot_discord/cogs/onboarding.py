# app/bot_discord/cogs/onboarding.py
"""
Accueil des nouveaux membres :
  - en DM, un embed proposant de lier son compte NAVIRE existant, ou d'en
    créer un sur navire-ai.com si besoin (bouton lien) ;
  - dans #notificator, une annonce publique + une présentation brève des
    salons, même si le DM échoue (MP fermés).
"""

from __future__ import annotations

import discord
from discord.ext import commands

from app.bot_discord.config import COLOR_PRIMARY, NOTIFICATOR_CHANNEL_ID
from app.bot_discord.cogs.sync_account import SITE_URL, SYNC_CHANNEL_NAME, SyncAccountModal

CHANNELS_PRESENTATION = (
    f"**#newsletter** — les actus juridiques du jour\n"
    f"**#place-publique** — discussion générale\n"
    f"**#aide-aux-devoirs** — pose tes questions de droit\n"
    f"**#classement** — ton ELO et ta progression\n"
    f"**#{SYNC_CHANNEL_NAME}** — relie ton compte NAVIRE"
)

ONBOARDING_LINK_CUSTOM_ID = "onboarding:link"


class OnboardingLinkView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="Créer un compte NAVIRE",
                style=discord.ButtonStyle.link,
                url=f"{SITE_URL}/register",
            )
        )

    @discord.ui.button(
        label="Lier mon compte NAVIRE",
        style=discord.ButtonStyle.primary,  # bleu
        emoji="🔗",
        custom_id=ONBOARDING_LINK_CUSTOM_ID,
    )
    async def link_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(SyncAccountModal())


def build_dm_embed(member: discord.Member, guild: discord.Guild) -> discord.Embed:
    embed = discord.Embed(
        title=f"Bienvenue sur NAVIRE, {member.display_name} !",
        description=(
            f"Tu viens de rejoindre **{guild.name}**, la communauté des étudiants en droit NAVIRE.\n\n"
            "Relie ton compte navire-ai.com à ton Discord pour débloquer ton rôle, ton classement "
            "et suivre ta progression directement ici. Pas encore de compte ? Tu peux en créer un "
            "en un clic ci-dessous.\n\n"
            f"{CHANNELS_PRESENTATION}"
        ),
        color=COLOR_PRIMARY,
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.set_footer(text="NAVIRE — navire-ai.com")
    return embed


def build_notificator_embed(member: discord.Member) -> discord.Embed:
    embed = discord.Embed(
        description=(
            f"👋 {member.mention} nous a rejoint ! Bienvenue sur la communauté NAVIRE.\n\n"
            f"{CHANNELS_PRESENTATION}"
        ),
        color=COLOR_PRIMARY,
    )
    embed.set_author(name=str(member), icon_url=member.display_avatar.url)
    embed.timestamp = discord.utils.utcnow()
    return embed


class OnboardingCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return

        try:
            await member.send(embed=build_dm_embed(member, member.guild), view=OnboardingLinkView())
        except (discord.Forbidden, discord.HTTPException):
            pass  # MP fermés — l'annonce dans #notificator suffit

        channel = self.bot.get_channel(NOTIFICATOR_CHANNEL_ID)
        if channel:
            try:
                await channel.send(embed=build_notificator_embed(member))
            except discord.HTTPException as e:
                print(f"[onboarding] Erreur #notificator : {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(OnboardingCog(bot))
