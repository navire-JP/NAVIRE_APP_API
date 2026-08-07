# app/bot_discord/cogs/sync_roles.py

import discord
from discord.ext import commands

from app.bot_discord.config import PLAN_TO_ROLE
from app.bot_discord.utils.api_client import get_navire_user


class SyncRolesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def sync_member_role(self, member: discord.Member) -> str:
        """Relit le plan côté API puis applique le rôle correspondant."""
        data = await get_navire_user(str(member.id))
        if not data:
            return "not_linked"

        return await self.apply_plan_role(member, data.get("plan", "free"))

    async def apply_plan_role(self, member: discord.Member, plan: str) -> str:
        """
        Applique le rôle d'abonnement correspondant à `plan` (déjà connu).

        Retire d'abord tous les rôles de PLAN_TO_ROLE pour garantir l'exclusivité,
        puis pose celui du plan courant. `plan="free"` retire simplement tout.
        Les rôles Prép'AdJuris par matière ne sont pas concernés (voir role_sync.py).
        """
        role_name = PLAN_TO_ROLE.get(plan)

        for rn in PLAN_TO_ROLE.values():
            if rn == role_name:
                continue
            role = discord.utils.get(member.guild.roles, name=rn)
            if role and role in member.roles:
                try:
                    await member.remove_roles(role, reason="Sync plan NAVIRE")
                except discord.Forbidden:
                    pass

        if role_name:
            role = discord.utils.get(member.guild.roles, name=role_name)
            if role and role not in member.roles:
                try:
                    await member.add_roles(role, reason=f"Plan NAVIRE : {plan}")
                except discord.Forbidden:
                    pass

        return plan

    # ── //link : supprimée ───────────────────────────────────────────────────
    # Elle liait un compte sur le seul identifiant NAVIRE, un nombre séquentiel
    # que n'importe qui pouvait deviner pour s'attribuer l'abonnement d'un
    # autre. La liaison passe désormais par le bouton du salon dédié, qui exige
    # identifiant + email + code à usage unique (voir cogs/sync_account.py).

    # ── //sync ───────────────────────────────────────────────────────────────

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