"""Discord strategy button + modal handlers — Phase 3 plan Task 1.14.

When a strategy notification card is posted, it carries 4 buttons:

  [⏸ Pause]  [▶ Resume]  [🛑 Wind Down]  [🔧 Adjust…]

The first three are one-click actions. [Adjust] opens a discord.py
Modal where the operator types a params-patch JSON; on submit, the
patch is merged into the strategy's params via adjust_params.

custom_id format: 'strat_<action>:<strategy_id>'

The button parser deliberately raises UnknownStrategyButtonError on
non-strategy custom_ids so the existing listener (proposal approve/
reject buttons) can keep dispatching them via the same on_interaction
path. A "not mine" miss propagates to the next handler.
"""
from __future__ import annotations

import json
from typing import Any

import discord
from discord import ui


class UnknownStrategyButtonError(Exception):
    """Raised when parse_strategy_button_id sees a non-strategy custom_id."""


class ParamsPatchValidationError(ValueError):
    """Raised when the adjust modal's payload isn't a valid JSON object."""


_LEGAL_ACTIONS = {"pause", "resume", "wind_down", "adjust"}
_PREFIX_TO_ACTION = {
    "strat_pause": "pause",
    "strat_resume": "resume",
    "strat_winddown": "wind_down",
    "strat_adjust": "adjust",
}


def parse_strategy_button_id(custom_id: str) -> tuple[str, int]:
    """Parse 'strat_<action>:<strategy_id>'. Returns (action, strategy_id).
    Raises UnknownStrategyButtonError if the custom_id isn't recognized."""
    if ":" not in custom_id:
        raise UnknownStrategyButtonError(custom_id)
    prefix, raw_id = custom_id.split(":", 1)
    if prefix not in _PREFIX_TO_ACTION:
        raise UnknownStrategyButtonError(custom_id)
    try:
        sid = int(raw_id)
    except ValueError as e:
        raise UnknownStrategyButtonError(custom_id) from e
    return _PREFIX_TO_ACTION[prefix], sid


def validate_params_patch(payload: str) -> dict[str, Any]:
    """Parse + validate the adjust-modal's JSON payload. Must be a JSON
    object (dict). Lists, strings, and non-JSON are rejected."""
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as e:
        raise ParamsPatchValidationError(f"invalid json: {e}") from e
    if not isinstance(parsed, dict):
        raise ParamsPatchValidationError(
            f"params patch must be a JSON object, got {type(parsed).__name__}"
        )
    return parsed


# --- Views and modals (declarative) -------------------------------------


class StrategyCardView(ui.View):
    """Persistent view (timeout=None) attached to strategy notification
    cards. The four buttons drive lifecycle + params adjustment.

    Button callbacks are intentionally minimal — the heavy lifting
    (state transitions, audit) lives in the MCP active commands which
    these delegate to via discord/strategies_handlers.
    """

    def __init__(self, strategy_id: int):
        super().__init__(timeout=None)
        self.strategy_id = strategy_id
        # Four buttons. custom_ids are what the listener parses.
        self.add_item(ui.Button(
            label="Pause", style=discord.ButtonStyle.secondary,
            custom_id=f"strat_pause:{strategy_id}",
        ))
        self.add_item(ui.Button(
            label="Resume", style=discord.ButtonStyle.success,
            custom_id=f"strat_resume:{strategy_id}",
        ))
        self.add_item(ui.Button(
            label="Wind Down", style=discord.ButtonStyle.danger,
            custom_id=f"strat_winddown:{strategy_id}",
        ))
        self.add_item(ui.Button(
            label="Adjust…", style=discord.ButtonStyle.primary,
            custom_id=f"strat_adjust:{strategy_id}",
        ))


def build_strategy_card_view(strategy_id: int) -> StrategyCardView:
    """Factory wrapper used by callers that compose strategy cards.
    Tests assert against this rather than constructing the View
    directly so the contract is in one place."""
    return StrategyCardView(strategy_id=strategy_id)


class AdjustParamsModal(ui.Modal, title="Adjust strategy params"):
    """Modal popped open when [Adjust…] is clicked. Single multi-line
    text input where the operator pastes a JSON object representing
    the params patch. Validated on submit; merged into strategy.params
    via the MCP adjust_params tool.
    """

    def __init__(self, strategy_id: int):
        super().__init__(timeout=300)
        self.strategy_id = strategy_id
        self.payload = ui.TextInput(
            label="Params patch (JSON object)",
            style=discord.TextStyle.paragraph,
            placeholder='{"high": 75000, "levels": 6}',
            required=True,
            max_length=2000,
        )
        self.rationale = ui.TextInput(
            label="Rationale (why)",
            style=discord.TextStyle.short,
            placeholder="extending grid ceiling per breakout",
            required=True,
            max_length=300,
        )
        self.add_item(self.payload)
        self.add_item(self.rationale)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from trading_sandwich.mcp.tools import strategies_command

        try:
            patch = validate_params_patch(self.payload.value)
        except ParamsPatchValidationError as e:
            await interaction.response.send_message(
                f":x: invalid patch: {e}", ephemeral=True,
            )
            return

        result = await strategies_command.adjust_params(
            strategy_id=self.strategy_id,
            params=patch,
            rationale=self.rationale.value,
        )
        if result["status"] != "ok":
            await interaction.response.send_message(
                f":x: adjust failed: {result.get('error')} — "
                f"{result.get('message', '')}",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f":wrench: strategy `{self.strategy_id}` params updated. "
            f"new params: `{json.dumps(result['params'])}`. "
            f"rationale: {self.rationale.value}",
            ephemeral=True,
        )


# --- Listener dispatch helpers (called from listener.on_interaction) ----


async def handle_strategy_button(
    interaction: discord.Interaction, action: str, strategy_id: int,
) -> None:
    """Dispatch a one-click strategy button. The 'adjust' action is
    handled by opening AdjustParamsModal instead of mutating directly."""
    from trading_sandwich.discord import strategies_handlers as h

    if action == "pause":
        reply = await h.handle_strategies_pause(
            strategy_id=strategy_id,
            reason="paused via Discord button",
        )
        await interaction.response.send_message(reply[:1900], ephemeral=True)
    elif action == "resume":
        reply = await h.handle_strategies_resume(
            strategy_id=strategy_id,
            rationale="resumed via Discord button",
        )
        await interaction.response.send_message(reply[:1900], ephemeral=True)
    elif action == "wind_down":
        from trading_sandwich.mcp.tools import strategies_command
        result = await strategies_command.wind_down_strategy(
            strategy_id=strategy_id, urgency="graceful",
            rationale="wind down via Discord button",
        )
        if result["status"] == "ok":
            msg = f":octagonal_sign: strategy `{strategy_id}` winding down (graceful)"
        else:
            msg = (f":x: cannot wind down `{strategy_id}` — "
                   f"`{result.get('error')}`: {result.get('message', '')}")
        await interaction.response.send_message(msg[:1900], ephemeral=True)
    elif action == "adjust":
        await interaction.response.send_modal(AdjustParamsModal(strategy_id))
    else:
        # Caught upstream by parse_strategy_button_id, but defense in depth.
        raise UnknownStrategyButtonError(action)
