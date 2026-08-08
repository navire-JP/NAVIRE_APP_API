# app/bot_discord/cogs/help_commands.py
"""
Aide :
  - `/help` : liste publique de toutes les commandes, groupées par catégorie,
    construite dynamiquement depuis les commandes enregistrées (rien à
    maintenir à la main quand un cog ajoute une commande).
  - `/setup_helpmenu` (admin) : poste dans HELPMENU_CHANNEL_ID un panneau
    avec liste déroulante des catégories ; le choix ouvre la liste des
    commandes de la catégorie, en éphémère. Réservé aux admins, aussi bien
    pour poser le panneau que pour l'utiliser.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from app.bot_discord.config import ADMIN_ROLE_ID, COLOR_ADMIN, COLOR_PRIMARY, HELPMENU_CHANNEL_ID

CATEGORY_LABELS = {
    "ClassementCog":    "🏆 Classement",
    "SyncRolesCog":     "🔗 Connexion compte",
    "SyncAccountCog":   "🔗 Connexion compte",
    "PrepaAdjurisCog":  "📚 Prép'AdJuris",
    "CommandsCog":      "🎮 Commandes",
    "NavireAICog":      "🤖 NAVIRE AI",
    "HelpCommandsCog":  "❓ Aide",
}

HELPMENU_SELECT_CUSTOM_ID = "helpmenu:select"


def _is_admin(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    role = discord.utils.get(member.guild.roles, id=ADMIN_ROLE_ID)
    return role in member.roles if role else False


def build_categories(bot: commands.Bot) -> dict[str, list[tuple[str, str]]]:
    """{catégorie: [(syntaxe, description), …]} à partir des commandes vivantes du bot."""
    categories: dict[str, list[tuple[str, str]]] = {}

    for cmd in bot.tree.get_commands():
        cog_name = cmd.binding.__class__.__name__ if cmd.binding else "Autre"
        label = CATEGORY_LABELS.get(cog_name, cog_name)
        categories.setdefault(label, []).append((f"/{cmd.name}", cmd.description or "—"))

    for cmd in bot.commands:
        if cmd.hidden:
            continue
        cog_name = cmd.cog.__class__.__name__ if cmd.cog else "Autre"
        label = CATEGORY_LABELS.get(cog_name, cog_name)
        desc = cmd.help.strip().splitlines()[0] if cmd.help else "—"
        categories.setdefault(label, []).append((f"//{cmd.name}", desc))

    return dict(sorted(categories.items()))


class HelpCategorySelect(discord.ui.Select):
    def __init__(self) -> None:
        # Options par défaut au moment de l'enregistrement de la vue
        # persistante — le contenu affiché après sélection est, lui, toujours
        # recalculé à la volée (cf. callback).
        options = [discord.SelectOption(label="Catégories", value="_placeholder")]
        super().__init__(
            placeholder="Choisis une catégorie de commandes…",
            options=options,
            custom_id=HELPMENU_SELECT_CUSTOM_ID,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member) or not _is_admin(interaction.user):
            await interaction.response.send_message("Réservé aux admins.", ephemeral=True)
            return

        categories = build_categories(interaction.client)
        label = self.values[0]
        commands_list = categories.get(label, [])

        embed = discord.Embed(title=label, color=COLOR_ADMIN)
        embed.description = (
            "\n".join(f"`{syntax}` — {desc}" for syntax, desc in commands_list) or "Aucune commande."
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    def refresh_options(self, categories: dict[str, list[tuple[str, str]]]) -> None:
        self.options = [
            discord.SelectOption(label=label, value=label) for label in list(categories.keys())[:25]
        ] or [discord.SelectOption(label="Catégories", value="_placeholder")]


class HelpMenuView(discord.ui.View):
    def __init__(self, categories: dict[str, list[tuple[str, str]]] | None = None) -> None:
        super().__init__(timeout=None)
        self.select = HelpCategorySelect()
        if categories:
            self.select.refresh_options(categories)
        self.add_item(self.select)


class HelpCommandsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="help", description="Liste les commandes disponibles sur NAVIRE.")
    async def help_cmd(self, interaction: discord.Interaction) -> None:
        categories = build_categories(self.bot)
        embed = discord.Embed(title="❓ Commandes NAVIRE", color=COLOR_PRIMARY)
        for label, cmds in categories.items():
            value = "\n".join(f"`{syntax}` — {desc}" for syntax, desc in cmds[:10]) or "—"
            embed.add_field(name=label, value=value, inline=False)
        embed.set_footer(text="Version interactive : #helpmenu (admin)")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="setup_helpmenu", description="Installe le menu d'aide interactif (admin).")
    @app_commands.guild_only()
    async def setup_helpmenu(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member) or not _is_admin(interaction.user):
            await interaction.response.send_message("Réservé aux admins.", ephemeral=True)
            return

        channel = self.bot.get_channel(HELPMENU_CHANNEL_ID)
        if not channel:
            await interaction.response.send_message(
                f"Salon #helpmenu introuvable (ID {HELPMENU_CHANNEL_ID}).", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            async for message in channel.history(limit=50):
                if message.author.id == self.bot.user.id and message.embeds:
                    await message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass

        categories = build_categories(self.bot)
        embed = discord.Embed(
            title="📋 Centre de commandes NAVIRE",
            description="Sélectionne une catégorie ci-dessous pour voir les commandes associées.\nRéservé au staff.",
            color=COLOR_ADMIN,
        )

        try:
            await channel.send(embed=embed, view=HelpMenuView(categories))
        except discord.Forbidden:
            await interaction.followup.send(f"Impossible d'écrire dans {channel.mention}.", ephemeral=True)
            return

        await interaction.followup.send(f"Menu d'aide installé dans {channel.mention}.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCommandsCog(bot))
