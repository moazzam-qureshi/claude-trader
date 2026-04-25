"""Discord bot listener. Receives button interactions; flips proposal state."""
from __future__ import annotations

import os
from uuid import UUID

import discord


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


class TradingBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(intents=intents)

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = interaction.data.get("custom_id", "") if interaction.data else ""
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
