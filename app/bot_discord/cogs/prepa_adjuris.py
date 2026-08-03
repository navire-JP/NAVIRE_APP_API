# app/bot_discord/cogs/prepa_adjuris.py
"""
Flux de liaison de compte Prép'AdJuris — sans commande texte.

Un message fixe (posté via //adjuris-panel, admin only) contient un bouton
persistant "Lier mon compte NAVIRE". Le clic ouvre un Modal (email + code),
dont la soumission appelle le backend pour valider le code et attribuer les
rôles de toutes les matières actives de l'utilisateur.
"""

import discord
from discord.ext import commands

from app.bot_discord.config import ADMIN_ROLE_ID
from app.bot_discord.utils.api_client import link_adjuris_discord

LINK_BUTTON_CUSTOM_ID = "prepa_adjuris:link_account"


def _is_admin(ctx: commands.Context) -> bool:
    role = discord.utils.get(ctx.guild.roles, id=ADMIN_ROLE_ID)
    return role in ctx.author.roles if role else False


class LinkAccountModal(discord.ui.Modal, title="Lier mon compte NAVIRE"):
    email = discord.ui.TextInput(
        label="Email du compte NAVIRE",
        placeholder="toi@exemple.fr",
        required=True,
    )
    code = discord.ui.TextInput(
        label="Code reçu par email",
        placeholder="A1B2C3D4",
        max_length=16,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            result = await link_adjuris_discord(
                str(interaction.user.id),
                self.email.value.strip(),
                self.code.value.strip().upper(),
            )
        except Exception:
            await interaction.response.send_message(
                "❌ Erreur de connexion au serveur NAVIRE. Réessaie dans quelques instants.",
                ephemeral=True,
            )
            return

        if result and result.get("ok"):
            matieres = result.get("matieres") or []
            detail = f" ({len(matieres)} matière(s) débloquée(s))" if matieres else ""
            await interaction.response.send_message(
                f"✅ Compte lié avec succès !{detail}",
                ephemeral=True,
            )
        else:
            message = (result or {}).get("message", "Code invalide, déjà utilisé ou expiré.")
            await interaction.response.send_message(f"❌ {message}", ephemeral=True)


class LinkAccountView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Lier mon compte NAVIRE",
        emoji="🔗",
        style=discord.ButtonStyle.primary,
        custom_id=LINK_BUTTON_CUSTOM_ID,
    )
    async def link_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(LinkAccountModal())


class PrepaAdjurisCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="adjuris-panel")
    async def adjuris_panel(self, ctx: commands.Context):
        """//adjuris-panel — poste le message fixe de liaison de compte (admin)."""
        if not _is_admin(ctx):
            return await ctx.send("❌ Réservé aux admins.", delete_after=5)

        embed = discord.Embed(
            title="Prép'AdJuris — Lie ton compte NAVIRE",
            description=(
                "Clique sur le bouton ci-dessous, entre l'email de ton compte NAVIRE "
                "et le code reçu par email pour débloquer l'accès à tes matières."
            ),
            color=discord.Color.blurple(),
        )
        await ctx.send(embed=embed, view=LinkAccountView())


async def setup(bot: commands.Bot):
    await bot.add_cog(PrepaAdjurisCog(bot))
