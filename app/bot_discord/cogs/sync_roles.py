# app/bot_discord/cogs/sync_roles.py

import discord
from discord.ext import commands

from app.bot_discord.config import PLAN_TO_ROLE, ADMIN_ROLE_ID
from app.bot_discord.utils.api_client import get_navire_user, link_discord


def _is_admin(ctx: commands.Context) -> bool:
    role = discord.utils.get(ctx.guild.roles, id=ADMIN_ROLE_ID)
    return role in ctx.author.roles if role else False


class SyncRolesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def sync_member_role(self, member: discord.Member) -> str:
        data = await get_navire_user(str(member.id))
        if not data:
            return "not_linked"

        plan      = data.get("plan", "free")
        role_name = PLAN_TO_ROLE.get(plan)

        for rn in PLAN_TO_ROLE.values():
            role = discord.utils.get(member.guild.roles, name=rn)
            if role and role in member.roles:
                try:
                    await member.remove_roles(role, reason="Sync plan NAVIRE")
                except discord.Forbidden:
                    pass

        if role_name:
            role = discord.utils.get(member.guild.roles, name=role_name)
            if role:
                try:
                    await member.add_roles(role, reason=f"Plan NAVIRE : {plan}")
                except discord.Forbidden:
                    pass

        return plan

    # ── Commande user : //link <user_id_navire> ──────────────────────────────

    @commands.command(name="link")
    async def link(self, ctx: commands.Context, target: str = "", navire_id: str = ""):
        """
        Usage user  : //link <user_id_navire>
        Usage admin : //link @mention <user_id_navire>
        """
        await ctx.message.delete(delay=2)

        # ── Cas admin : //link @mention <user_id_navire> ──────────────────────
        if ctx.message.mentions and navire_id:
            if not _is_admin(ctx):
                return await ctx.send("❌ Réservé aux admins.", delete_after=5)

            if not navire_id.isdigit():
                return await ctx.send(
                    "❌ Usage admin : `//link @mention <user_id_navire>`",
                    delete_after=10,
                )

            member  = ctx.message.mentions[0]
            discord_id = str(member.id)

            try:
                result = await link_discord(int(navire_id), discord_id)
                if result.get("ok"):
                    await ctx.send(
                        f"✅ **{member.display_name}** lié au compte NAVIRE `#{navire_id}`.",
                        delete_after=10,
                    )
                else:
                    await ctx.send("❌ Liaison échouée.", delete_after=10)
            except Exception as e:
                await ctx.send(f"❌ Erreur : {e}", delete_after=10)
            return

        # ── Cas user : //link <user_id_navire> ───────────────────────────────
        token = target  # premier argument = user_id_navire
        if not token.isdigit():
            return await ctx.send(
                "❌ Usage : `//link <ton_user_id_navire>`\n"
                "Trouve ton ID dans ton profil sur navire-ai.com.",
                delete_after=10,
            )

        try:
            result = await link_discord(int(token), str(ctx.author.id))
            if result.get("ok"):
                await ctx.send(
                    "✅ Compte lié ! Tape `//sync` pour mettre à jour ton rôle.",
                    delete_after=10,
                )
            else:
                await ctx.send("❌ Liaison échouée.", delete_after=10)
        except Exception as e:
            await ctx.send(f"❌ Erreur : {e}", delete_after=10)

    # ── Commande user : //sync ───────────────────────────────────────────────

    @commands.command(name="sync")
    async def sync(self, ctx: commands.Context):
        """//sync — Synchronise ton rôle Discord avec ton plan NAVIRE."""
        await ctx.message.delete(delay=2)

        data = await get_navire_user(str(ctx.author.id))
        if not data:
            return await ctx.send(
                "❌ Compte non lié. Tape `//link <user_id_navire>`.",
                delete_after=10,
            )

        await self.sync_member_role(ctx.author)

        embed = discord.Embed(title="✅ Synchronisation NAVIRE", color=discord.Color.green())
        embed.add_field(name="Plan",   value=data.get("plan", "free").capitalize(), inline=True)
        embed.add_field(name="ELO",    value=str(data.get("elo", 0)),               inline=True)
        embed.add_field(name="Streak", value=f"{data.get('discord_streak', 0)}j",   inline=True)
        await ctx.send(embed=embed, delete_after=15)


async def setup(bot: commands.Bot):
    await bot.add_cog(SyncRolesCog(bot))