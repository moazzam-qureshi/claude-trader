"""Discord bot listener. Receives button interactions; flips proposal state.

Also hosts /settings and /safety slash commands for the three-tier
policy repo. Handlers live in `discord.settings_handlers`; this module
is the discord.py glue that registers them and forwards interaction
context (actor user id, configured operator id).
"""
from __future__ import annotations

import os
from uuid import UUID

import discord
from discord import app_commands


def parse_custom_id(custom_id: str) -> tuple[str, UUID]:
    """Expected format: '<action>:<uuid>'. Raises on mismatch."""
    if ":" not in custom_id:
        raise ValueError(f"invalid custom_id {custom_id!r}")
    action, raw = custom_id.split(":", 1)
    if action not in ("approve", "reject", "details"):
        raise ValueError(f"unknown action {action!r}")
    return action, UUID(raw)


def validate_operator(interaction) -> bool:
    """Compare the interacting user's id against env DISCORD_OPERATOR_ID."""
    expected = os.environ.get("DISCORD_OPERATOR_ID", "")
    return str(interaction.user.id) == expected


def _operator_id() -> str:
    return os.environ.get("DISCORD_OPERATOR_ID", "")


def _register_settings_commands(tree: app_commands.CommandTree) -> None:
    """Wire /settings and /safety slash commands onto the command tree."""
    from trading_sandwich.discord import settings_handlers as h

    settings_grp = app_commands.Group(
        name="settings",
        description="Three-tier policy settings (Tier 3 — non-safety)",
    )
    safety_grp = app_commands.Group(
        name="safety",
        description="Tier 2 operator-safety settings (operator-only)",
    )

    @settings_grp.command(name="list", description="List policy settings (optionally filtered by prefix)")
    async def _settings_list(interaction: discord.Interaction, prefix: str = ""):
        await interaction.response.defer(ephemeral=True, thinking=True)
        reply = await h.handle_settings_list(prefix=prefix)
        await interaction.followup.send(reply[:1900], ephemeral=True)

    @settings_grp.command(name="get", description="Read one policy setting by dotted-path key")
    async def _settings_get(interaction: discord.Interaction, key: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        reply = await h.handle_settings_get(key=key)
        await interaction.followup.send(reply[:1900], ephemeral=True)

    @settings_grp.command(name="set", description="Mutate a Tier 3 setting (rationale required)")
    async def _settings_set(interaction: discord.Interaction, key: str, value: str, rationale: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        reply = await h.handle_settings_set(key=key, value_str=value, rationale=rationale)
        await interaction.followup.send(reply[:1900], ephemeral=True)

    @safety_grp.command(name="list", description="List Tier 2 safety keys (current vs file seed)")
    async def _safety_list(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        reply = await h.handle_safety_list()
        await interaction.followup.send(reply[:1900], ephemeral=True)

    @safety_grp.command(name="set", description="Mutate a Tier 2 key (operator-only)")
    async def _safety_set(interaction: discord.Interaction, key: str, value: str, rationale: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        reply = await h.handle_safety_set(
            actor_id=str(interaction.user.id),
            operator_id=_operator_id(),
            key=key, value_str=value, rationale=rationale,
        )
        await interaction.followup.send(reply[:1900], ephemeral=True)

    @safety_grp.command(name="reset", description="Restore the file-seed value for a Tier 2 key (operator-only)")
    async def _safety_reset(interaction: discord.Interaction, key: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        reply = await h.handle_safety_reset(
            actor_id=str(interaction.user.id),
            operator_id=_operator_id(),
            key=key,
        )
        await interaction.followup.send(reply[:1900], ephemeral=True)

    tree.add_command(settings_grp)
    tree.add_command(safety_grp)


def _register_strategies_commands(tree: app_commands.CommandTree) -> None:
    """Wire /strategies, /regime, /equity, /decisions slash commands
    onto the command tree. Phase 3 plan Task 1.13."""
    from trading_sandwich.discord import strategies_handlers as h

    strategies_grp = app_commands.Group(
        name="strategies",
        description="View + control mechanical strategy fleet",
    )
    regime_grp = app_commands.Group(
        name="regime",
        description="View + override regime classifications",
    )

    @strategies_grp.command(name="list", description="List active + paused strategies")
    async def _strategies_list(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        reply = await h.handle_strategies_list()
        await interaction.followup.send(reply[:1900], ephemeral=True)

    @strategies_grp.command(name="pause", description="Pause an active strategy")
    async def _strategies_pause(
        interaction: discord.Interaction, strategy_id: int, reason: str,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        reply = await h.handle_strategies_pause(
            strategy_id=strategy_id, reason=reason,
        )
        await interaction.followup.send(reply[:1900], ephemeral=True)

    @strategies_grp.command(name="resume", description="Resume a paused strategy")
    async def _strategies_resume(
        interaction: discord.Interaction, strategy_id: int, rationale: str,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        reply = await h.handle_strategies_resume(
            strategy_id=strategy_id, rationale=rationale,
        )
        await interaction.followup.send(reply[:1900], ephemeral=True)

    @regime_grp.command(name="override", description="Override a symbol's regime (operator-only)")
    async def _regime_override(
        interaction: discord.Interaction,
        symbol: str, regime: str, duration_hours: int, rationale: str,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        reply = await h.handle_regime_override(
            actor_id=str(interaction.user.id),
            operator_id=_operator_id(),
            symbol=symbol, regime=regime,
            duration_hours=duration_hours, rationale=rationale,
        )
        await interaction.followup.send(reply[:1900], ephemeral=True)

    @tree.command(name="equity", description="Account allocation across strategies")
    async def _equity(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        reply = await h.handle_equity()
        await interaction.followup.send(reply[:1900], ephemeral=True)

    decisions_grp = app_commands.Group(
        name="decisions",
        description="Recent portfolio decisions",
    )

    @decisions_grp.command(name="last", description="Recent portfolio decisions")
    async def _decisions_last(
        interaction: discord.Interaction, duration: str = "24h",
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        reply = await h.handle_decisions_last(duration=duration)
        await interaction.followup.send(reply[:1900], ephemeral=True)

    tree.add_command(strategies_grp)
    tree.add_command(regime_grp)
    tree.add_command(decisions_grp)


class TradingBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        _register_settings_commands(self.tree)
        _register_strategies_commands(self.tree)

    async def setup_hook(self) -> None:
        # Sync slash commands on startup. Global sync can take ~1h to
        # propagate — for faster dev iteration, set DISCORD_GUILD_ID and
        # we'll sync to that guild only.
        guild_id = os.environ.get("DISCORD_GUILD_ID")
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = interaction.data.get("custom_id", "") if interaction.data else ""

        # First try strategy buttons (Phase 3 plan Task 1.14). On miss
        # fall through to the legacy proposal approve/reject/details.
        from trading_sandwich.discord.strategies_buttons import (
            UnknownStrategyButtonError,
            handle_strategy_button,
            parse_strategy_button_id,
        )
        try:
            strat_action, strategy_id = parse_strategy_button_id(custom_id)
        except UnknownStrategyButtonError:
            pass
        else:
            await handle_strategy_button(interaction, strat_action, strategy_id)
            return

        try:
            action, proposal_id = parse_custom_id(custom_id)
        except ValueError:
            return
        if not validate_operator(interaction):
            await interaction.response.send_message("not authorized", ephemeral=True)
            return
        from trading_sandwich.discord.approval import (
            handle_approve,
            handle_details,
            handle_reject,
        )
        if action == "approve":
            await handle_approve(interaction, proposal_id)
        elif action == "reject":
            await handle_reject(interaction, proposal_id)
        elif action == "details":
            await handle_details(interaction, proposal_id)


def run() -> None:
    """Entrypoint for the discord-listener service container."""
    token = os.environ["DISCORD_BOT_TOKEN"]
    TradingBot().run(token)


if __name__ == "__main__":
    run()
