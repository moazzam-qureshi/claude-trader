from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.anyio
async def test_post_webhook_uses_httpx_post():
    from trading_sandwich.discord.webhook import post_webhook

    with patch("trading_sandwich.discord.webhook.httpx.AsyncClient") as cli:
        instance = MagicMock()
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        instance.post = AsyncMock(return_value=MagicMock(status_code=204))
        cli.return_value = instance
        status = await post_webhook("https://example.com/hook", {"content": "hi"})
        assert status == 204
        instance.post.assert_awaited_once()
