# app/bot_discord/cogs/deepwork.py
"""
Deep work — sessions de travail dans le salon vocal dédié.

Entrer dans le vocal ouvre une session : le membre reçoit un MP avec un
chronomètre qui se réécrit tout seul et choisit un objectif de durée
(20 min, 1 h, 1 h 30, 2 h — ou session libre). Quitter le vocal clôt la
session et le MP devient un bilan : objectif atteint ou non, durée, totaux.

Le bot ne tient pas le temps lui-même : chaque rafraîchissement demande à
l'API le temps écoulé réel (`/discord/deepwork/tick`), calculé depuis le
`started_at` en base. Un redémarrage du bot ne fabrique donc jamais de durée
fantaisiste — les sessions orphelines sont marquées périmées au démarrage.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from app.bot_discord.config import (
    COLOR_ADMIN,
    COLOR_DEEPWORK,
    COLOR_MISS,
    COLOR_SUCCESS,
    DEEPWORK_CHANNEL_ID,
    DEEPWORK_LOG_CHANNEL_ID,
    DEEPWORK_REFRESH_SECONDS,
)
from app.bot_discord.utils.api_client import (
    deepwork_reset_stale,
    deepwork_set_goal,
    deepwork_set_message,
    deepwork_start,
    deepwork_stats,
    deepwork_stop,
    deepwork_tick,
)

logger = logging.getLogger(__name__)

# Libellés des objectifs proposés — mêmes valeurs que GOAL_CHOICES côté API.
GOAL_OPTIONS: list[tuple[int, str]] = [
    (20,  "20 min"),
    (60,  "1 h"),
    (90,  "1 h 30"),
    (120, "2 h"),
]

PROGRESS_BLOCKS = 12

# Nombre d'essais de la purge au démarrage (délais 5, 10, 20, 40, 60 s) : le
# temps que l'API du même processus commence à répondre.
DEEPWORK_BOOTSTRAP_ATTEMPTS = 6
SITE_PROFILE_URL = "https://navire-ai.com/profil"


# ============================================================
# Formatage
# ============================================================

def _chrono(seconds: int) -> str:
    """3 725 → « 01:02:05 »."""
    seconds = max(0, int(seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _human(seconds: int) -> str:
    """3 725 → « 1 h 02 », 900 → « 15 min »."""
    seconds = max(0, int(seconds))
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d}"


def _goal_text(goal_minutes: int | None) -> str:
    if not goal_minutes:
        return "Session libre"
    return dict(GOAL_OPTIONS).get(goal_minutes, _human(goal_minutes * 60))


def _progress_bar(elapsed: int, target: int) -> str:
    if target <= 0:
        return ""
    ratio = min(1.0, elapsed / target)
    filled = int(round(ratio * PROGRESS_BLOCKS))
    return "█" * filled + "░" * (PROGRESS_BLOCKS - filled) + f"  {int(ratio * 100)} %"


# ============================================================
# État en mémoire d'une session suivie
# ============================================================

@dataclass
class LiveSession:
    session_id: int
    member: discord.Member
    started_at: datetime
    channel_name: str
    message: discord.Message | None = None
    goal_minutes: int | None = None
    goal_chosen: bool = False          # l'utilisateur a répondu (objectif ou « libre »)
    goal_reached: bool = False
    elapsed: int = 0
    stats: dict = field(default_factory=dict)
    # Suivi affiché dans le salon vocal au lieu des MP (repli quand les MP
    # du membre sont fermés).
    in_channel: bool = False

    @property
    def discord_id(self) -> str:
        return str(self.member.id)


# ============================================================
# Boutons d'objectif (dans le MP)
# ============================================================

class GoalView(discord.ui.View):
    """
    Choix — et re-choix — de l'objectif. Les boutons restent actifs toute la
    session : passer de 1 h à 2 h en cours de route est un cas normal, l'API
    recalcule alors si l'objectif est atteint ou non.
    """

    def __init__(self, cog: "DeepWorkCog", live: LiveSession):
        super().__init__(timeout=None)
        self.cog = cog
        self.live = live

        for minutes, label in GOAL_OPTIONS:
            self.add_item(_GoalButton(minutes, label))
        self.add_item(_FreeButton())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.live.member.id:
            await interaction.response.send_message(
                "Cette session ne t'appartient pas.", ephemeral=True
            )
            return False
        return True

    async def apply(self, interaction: discord.Interaction, goal_minutes: int | None) -> None:
        live = self.live
        try:
            data = await deepwork_set_goal(live.session_id, live.discord_id, goal_minutes)
        except Exception as e:  # panne réseau/API — la session continue quand même
            logger.warning("[deepwork] objectif non enregistré (%s) : %s", live.session_id, e)
            await interaction.response.send_message(
                "Impossible d'enregistrer l'objectif pour l'instant, réessaie dans un instant. "
                "Ta session, elle, continue d'être décomptée.",
                ephemeral=True,
            )
            return

        if not data.get("ok"):
            await interaction.response.send_message(
                data.get("message", "Objectif non enregistré."), ephemeral=True
            )
            return

        session = data.get("session") or {}
        live.goal_minutes = session.get("goal_minutes")
        live.goal_chosen = True
        live.goal_reached = bool(session.get("goal_reached"))
        live.elapsed = int(data.get("elapsed_seconds") or live.elapsed)
        live.stats = data.get("stats") or live.stats

        await interaction.response.edit_message(embed=self.cog.running_embed(live), view=self)


class _GoalButton(discord.ui.Button):
    def __init__(self, minutes: int, label: str):
        super().__init__(label=label, style=discord.ButtonStyle.primary, custom_id=f"deepwork:goal:{minutes}")
        self.minutes = minutes

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.view.apply(interaction, self.minutes)


class _FreeButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Session libre",
            style=discord.ButtonStyle.secondary,
            custom_id="deepwork:goal:free",
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.view.apply(interaction, None)


# ============================================================
# Cog
# ============================================================

class DeepWorkCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.live: dict[int, LiveSession] = {}   # member.id → session suivie
        self._bootstrapped = False
        self.refresh_loop.change_interval(seconds=DEEPWORK_REFRESH_SECONDS)

    async def cog_load(self) -> None:
        self.refresh_loop.start()

    async def cog_unload(self) -> None:
        self.refresh_loop.cancel()

    # ── Embeds ───────────────────────────────────────────────────────────────

    def running_embed(self, live: LiveSession) -> discord.Embed:
        """Chronomètre en cours — état d'attente d'objectif inclus."""
        reached = live.goal_reached
        embed = discord.Embed(
            title="🎉 Objectif atteint — tu peux continuer" if reached
            else "🌊 Votre session deep work a commencé",
            color=COLOR_SUCCESS if reached else COLOR_DEEPWORK,
        )

        started_ts = int(live.started_at.timestamp())
        # Tant que rien n'a été choisi, l'objectif n'est pas « libre » : il est
        # en attente de réponse. Les deux états ne doivent pas se confondre.
        goal_field = _goal_text(live.goal_minutes) if live.goal_chosen else "à choisir ci-dessous ⬇️"

        embed.add_field(name="⏱️ Temps de travail", value=f"**{_chrono(live.elapsed)}**", inline=True)
        embed.add_field(name="🎯 Objectif", value=goal_field, inline=True)
        embed.add_field(name="🚀 Démarrée", value=f"<t:{started_ts}:R>", inline=True)

        if live.goal_minutes:
            target = live.goal_minutes * 60
            remaining = max(0, target - live.elapsed)
            embed.add_field(
                name="Progression",
                value=f"`{_progress_bar(live.elapsed, target)}`",
                inline=False,
            )
            if reached:
                extra = live.elapsed - target
                embed.add_field(
                    name="Temps bonus",
                    value=f"+{_human(extra)} au-delà de l'objectif" if extra > 0 else "Objectif tout juste atteint",
                    inline=False,
                )
            else:
                embed.add_field(
                    name="Reste à tenir",
                    value=f"**{_chrono(remaining)}** — fin prévue <t:{started_ts + target}:t>",
                    inline=False,
                )
        elif not live.goal_chosen:
            embed.description = (
                "Choisis ton objectif de session ci-dessous. "
                "Le chronomètre tourne déjà — il s'arrêtera quand tu quitteras le vocal."
            )
        else:
            embed.description = (
                "Session libre : aucun objectif à tenir, le temps est simplement compté."
            )

        stats = live.stats or {}
        if stats.get("total_sessions"):
            embed.add_field(
                name="Ton total",
                value=(
                    f"{stats.get('total_sessions', 0)} session(s) · "
                    f"{stats.get('total_label', '0 min')} · "
                    f"🔥 {stats.get('current_streak', 0)} j"
                ),
                inline=False,
            )

        embed.set_footer(text=f"#{live.channel_name} · mis à jour toutes les {DEEPWORK_REFRESH_SECONDS} s")
        embed.timestamp = discord.utils.utcnow()
        return embed

    def final_embed(self, live: LiveSession, data: dict) -> discord.Embed:
        session = data.get("session") or {}
        stats = data.get("stats") or {}
        duration = int(session.get("duration_seconds") or 0)
        goal_minutes = session.get("goal_minutes")
        reached = bool(session.get("goal_reached"))

        if data.get("too_short"):
            embed = discord.Embed(
                title="⏹️ Session deep work trop courte",
                description=(
                    f"Moins de {int(data.get('min_seconds', 60)) // 60 or 1} minute dans le vocal : "
                    "ce passage n'a pas été compté dans tes statistiques."
                ),
                color=COLOR_ADMIN,
            )
            embed.timestamp = discord.utils.utcnow()
            return embed

        if goal_minutes and reached:
            title, color = "✅ Objectif atteint — session terminée", COLOR_SUCCESS
            verdict = f"Objectif de **{_goal_text(goal_minutes)}** tenu. Bravo."
        elif goal_minutes:
            missing = max(0, goal_minutes * 60 - duration)
            title, color = "⏹️ Session terminée — objectif non atteint", COLOR_MISS
            verdict = (
                f"Objectif de **{_goal_text(goal_minutes)}** : il te manquait "
                f"**{_human(missing)}**. Le temps travaillé est quand même compté."
            )
        else:
            title, color = "⏹️ Session libre terminée", COLOR_DEEPWORK
            verdict = "Aucun objectif n'était fixé : le temps travaillé est enregistré."

        embed = discord.Embed(title=title, description=verdict, color=color)
        embed.add_field(name="⏱️ Durée", value=f"**{_chrono(duration)}**", inline=True)
        embed.add_field(name="🎯 Objectif", value=_goal_text(goal_minutes), inline=True)
        embed.add_field(name="🔥 Série", value=f"{stats.get('current_streak', 0)} jour(s)", inline=True)
        embed.add_field(
            name="Tes statistiques deep work",
            value=(
                f"• **{stats.get('total_sessions', 0)}** session(s) · **{stats.get('total_label', '0 min')}** au total\n"
                f"• Aujourd'hui : **{stats.get('today_label', '0 min')}** · 7 jours : **{stats.get('week_label', '0 min')}**\n"
                f"• Objectifs tenus : **{stats.get('goals_reached', 0)}/{stats.get('goals_set', 0)}** "
                f"({stats.get('goal_success_rate', 0)} %)\n"
                f"• Plus longue session : **{stats.get('longest_label', '0 min')}**"
            ),
            inline=False,
        )
        embed.set_footer(text="Retrouve tes stats sur ton profil NAVIRE")
        embed.timestamp = discord.utils.utcnow()
        return embed

    def stats_embed(self, username: str, data: dict) -> discord.Embed:
        stats = data.get("stats") or {}
        embed = discord.Embed(
            title=f"📊 Deep work — {username}",
            color=COLOR_DEEPWORK,
        )
        embed.add_field(name="Total", value=stats.get("total_label", "0 min"), inline=True)
        embed.add_field(name="Sessions", value=str(stats.get("total_sessions", 0)), inline=True)
        embed.add_field(name="Série", value=f"🔥 {stats.get('current_streak', 0)} j", inline=True)
        embed.add_field(name="Aujourd'hui", value=stats.get("today_label", "0 min"), inline=True)
        embed.add_field(name="7 derniers jours", value=stats.get("week_label", "0 min"), inline=True)
        embed.add_field(name="Moyenne", value=stats.get("average_label", "0 min"), inline=True)
        embed.add_field(
            name="Objectifs tenus",
            value=f"{stats.get('goals_reached', 0)}/{stats.get('goals_set', 0)} ({stats.get('goal_success_rate', 0)} %)",
            inline=True,
        )
        embed.add_field(name="Record", value=stats.get("longest_label", "0 min"), inline=True)
        embed.add_field(name="Meilleure série", value=f"{stats.get('best_streak', 0)} j", inline=True)

        active = data.get("active")
        if active:
            embed.description = (
                f"⏱️ Session en cours : **{_chrono(int(data.get('active_elapsed_seconds') or 0))}** "
                f"· objectif {_goal_text(active.get('goal_minutes'))}"
            )

        recent = data.get("recent") or []
        if recent:
            lines = []
            for row in recent:
                mark = "✅" if row.get("goal_reached") else ("•" if not row.get("goal_minutes") else "⏳")
                started = row.get("started_at")
                when = ""
                if started:
                    try:
                        when = f"<t:{int(datetime.fromisoformat(started).timestamp())}:d> "
                    except ValueError:
                        when = ""
                lines.append(f"{mark} {when}{row.get('duration_label', '')} · {row.get('goal_label', '')}")
            embed.add_field(name="Dernières sessions", value="\n".join(lines), inline=False)

        embed.set_footer(text="Statistiques complètes sur ton profil NAVIRE")
        return embed

    # ── Journal ──────────────────────────────────────────────────────────────

    async def _log(self, description: str) -> None:
        if not DEEPWORK_LOG_CHANNEL_ID:
            return
        channel = self.bot.get_channel(DEEPWORK_LOG_CHANNEL_ID)
        if not channel:
            return
        embed = discord.Embed(description=description, color=COLOR_ADMIN)
        embed.timestamp = discord.utils.utcnow()
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    # ── Démarrage / arrêt d'une session ──────────────────────────────────────

    async def _begin(self, member: discord.Member, channel: discord.VoiceChannel) -> None:
        if member.id in self.live:
            return  # déjà suivie (double événement Discord)

        try:
            data = await deepwork_start(
                str(member.id),
                guild_id=str(member.guild.id) if member.guild else "",
                channel_id=str(channel.id),
            )
        except Exception as e:
            logger.warning("[deepwork] démarrage impossible pour %s : %s", member.id, e)
            return

        if not data.get("ok"):
            # Compte non lié : on prévient une fois, sans bloquer l'accès au vocal.
            message = data.get("message")
            if message:
                try:
                    await member.send(message)
                except discord.HTTPException:
                    pass
            return

        session = data.get("session") or {}
        started_at = datetime.now(timezone.utc)
        raw_started = session.get("started_at")
        if raw_started:
            try:
                started_at = datetime.fromisoformat(raw_started)
            except ValueError:
                pass

        live = LiveSession(
            session_id=int(session["id"]),
            member=member,
            started_at=started_at,
            channel_name=channel.name,
            stats=data.get("stats") or {},
        )
        self.live[member.id] = live

        view = GoalView(self, live)
        try:
            live.message = await member.send(embed=self.running_embed(live), view=view)
        except discord.Forbidden:
            # MP fermés côté membre. La session est comptée quand même, et le
            # chronomètre bascule dans le salon vocal : sans ce repli, le
            # membre ne voit strictement rien et croit que rien n'a démarré.
            logger.info("[deepwork] MP fermés pour %s — repli sur le salon vocal", member.id)
            try:
                live.message = await channel.send(
                    content=(
                        f"{member.mention} — tes messages privés sont fermés, "
                        "ton suivi deep work s'affiche donc ici."
                    ),
                    embed=self.running_embed(live),
                    view=view,
                )
                live.in_channel = True
            except discord.HTTPException as e:
                logger.warning("[deepwork] repli salon impossible pour %s : %s", member.id, e)
        except discord.HTTPException as e:
            logger.warning("[deepwork] MP non envoyé à %s : %s", member.id, e)

        # Trace explicite : sans elle, un membre qui dit « je n'ai rien reçu »
        # est indistinguable d'un envoi qui a échoué en silence.
        if live.message:
            logger.info(
                "[deepwork] session %s démarrée pour %s — suivi %s (message %s)",
                live.session_id, member.id, "salon" if live.in_channel else "MP", live.message.id,
            )
            try:
                await deepwork_set_message(live.session_id, live.discord_id, str(live.message.id))
            except Exception:
                pass  # confort de reprise seulement, rien de bloquant
        else:
            logger.warning(
                "[deepwork] session %s démarrée pour %s SANS aucun message affichable",
                live.session_id, member.id,
            )

        await self._log(f"🌊 {member.mention} a commencé une session deep work dans **{channel.name}**")

    async def _end(self, member: discord.Member) -> None:
        live = self.live.pop(member.id, None)
        if not live:
            return

        try:
            data = await deepwork_stop(live.session_id, live.discord_id)
        except Exception as e:
            logger.warning("[deepwork] clôture impossible (%s) : %s", live.session_id, e)
            return

        if not data.get("ok"):
            return

        if live.message:
            try:
                await live.message.edit(embed=self.final_embed(live, data), view=None)
            except discord.HTTPException:
                pass

        session = data.get("session") or {}
        stats = data.get("stats") or {}
        duration = _human(int(session.get("duration_seconds") or 0))
        verdict = "objectif atteint ✅" if session.get("goal_reached") else (
            "sans objectif" if not session.get("goal_minutes") else "objectif non atteint ⏳"
        )
        if data.get("too_short"):
            await self._log(f"⏹️ {member.mention} — passage deep work trop court, non compté")
        else:
            await self._log(
                f"⏹️ {member.mention} a terminé sa session deep work — **{duration}** ({verdict}) "
                f"· total **{stats.get('total_label', '0 min')}**"
            )

    # ── Événements ───────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        if member.bot or not DEEPWORK_CHANNEL_ID:
            return

        before_id = before.channel.id if before.channel else None
        after_id = after.channel.id if after.channel else None
        if before_id == after_id:
            return  # simple mute/deaf, pas un mouvement de salon

        if after_id == DEEPWORK_CHANNEL_ID:
            await self._begin(member, after.channel)
        elif before_id == DEEPWORK_CHANNEL_ID:
            await self._end(member)

    async def _reset_stale_with_retry(self) -> bool:
        """
        Purge des sessions orphelines, avec reprise.

        Bot et API tournent dans le même processus : au déploiement, le bot est
        connecté à Discord avant qu'uvicorn ne réponde sur l'URL publique, et
        le premier appel se prend un 502. Sans reprise, la purge est
        définitivement sautée à chaque déploiement et les sessions d'avant le
        redémarrage restent « actives » en base.
        """
        delay = 5
        for attempt in range(1, DEEPWORK_BOOTSTRAP_ATTEMPTS + 1):
            try:
                data = await deepwork_reset_stale([])
                closed = data.get("closed", 0) if isinstance(data, dict) else 0
                logger.info("[deepwork] purge au démarrage : %s session(s) périmée(s)", closed)
                return True
            except Exception as e:
                logger.warning(
                    "[deepwork] purge des sessions orphelines impossible (essai %s/%s) : %s",
                    attempt, DEEPWORK_BOOTSTRAP_ATTEMPTS, e,
                )
                if attempt == DEEPWORK_BOOTSTRAP_ATTEMPTS:
                    break
                await asyncio.sleep(delay)
                delay = min(60, delay * 2)

        # Échec définitif : le filet horaire côté API (expire_long_sessions)
        # finira par clôturer ces sessions, et start_session ferme de toute
        # façon la session active d'un membre qui revient.
        logger.warning("[deepwork] purge au démarrage abandonnée — reprise par le job horaire")
        return False

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """
        Reprise après (re)démarrage : les sessions restées ouvertes en base
        n'ont plus de chronomètre attaché, elles sont marquées périmées. Les
        membres actuellement dans le vocal repartent sur une session neuve.
        """
        if self._bootstrapped:
            return
        self._bootstrapped = True

        await self._reset_stale_with_retry()

        channel = self.bot.get_channel(DEEPWORK_CHANNEL_ID) if DEEPWORK_CHANNEL_ID else None
        if isinstance(channel, discord.VoiceChannel):
            for member in channel.members:
                if not member.bot:
                    await self._begin(member, channel)

    # ── Rafraîchissement du chronomètre ──────────────────────────────────────

    @tasks.loop(seconds=15)
    async def refresh_loop(self) -> None:
        for live in list(self.live.values()):
            try:
                data = await deepwork_tick(live.session_id, live.discord_id)
            except Exception as e:
                logger.debug("[deepwork] tick %s : %s", live.session_id, e)
                continue

            if not data.get("ok") or not data.get("still_active"):
                # Session fermée côté API (purge, arrêt manuel) : on cesse de la suivre.
                self.live.pop(live.member.id, None)
                continue

            session = data.get("session") or {}
            live.elapsed = int(data.get("elapsed_seconds") or 0)
            live.goal_minutes = session.get("goal_minutes")
            live.goal_reached = bool(session.get("goal_reached"))

            if not live.message:
                continue

            try:
                await live.message.edit(embed=self.running_embed(live))
            except discord.HTTPException:
                continue

            # Ping séparé au franchissement de l'objectif — inutile quand le
            # suivi est déjà dans le salon vocal (l'embed y passe au vert sous
            # les yeux du membre), et le MP y échouerait de toute façon.
            if data.get("goal_just_reached") and not live.in_channel:
                try:
                    await live.member.send(
                        f"🎉 **Objectif atteint** — {_goal_text(live.goal_minutes)} de deep work. "
                        "Tu peux t'arrêter ou continuer sur ta lancée : le temps supplémentaire est compté."
                    )
                except discord.HTTPException:
                    pass

    @refresh_loop.before_loop
    async def _before_refresh(self) -> None:
        await self.bot.wait_until_ready()

    # ── Commande ─────────────────────────────────────────────────────────────

    @app_commands.command(name="deepwork", description="Mes statistiques de deep work")
    async def deepwork_command(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            data = await deepwork_stats(str(interaction.user.id))
        except Exception as e:
            logger.warning("[deepwork] stats indisponibles pour %s : %s", interaction.user.id, e)
            await interaction.followup.send(
                "Statistiques indisponibles pour le moment, réessaie dans un instant.", ephemeral=True
            )
            return

        if not data.get("ok"):
            await interaction.followup.send(
                data.get("message", "Compte NAVIRE non lié.")
                + f"\nTes statistiques sont aussi visibles sur ton profil : {SITE_PROFILE_URL}",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            embed=self.stats_embed(data.get("username") or interaction.user.display_name, data),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(DeepWorkCog(bot))
