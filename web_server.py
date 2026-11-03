"""Telegram webhook server for Render (local runs can still use polling)."""
import hashlib
import hmac
import logging
import re
from urllib.parse import urlsplit

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

WEBHOOK_PATH = "/telegram/webhook"


def webhook_settings(environ, bot_token: str) -> tuple[str, str, int]:
    base_url = (environ.get("WEBHOOK_BASE_URL") or environ.get("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
        raise ValueError("WEBHOOK_BASE_URL yoki RENDER_EXTERNAL_URL: https://your-service.onrender.com kerak")
    secret = environ.get("WEBHOOK_SECRET", "").strip()
    if not secret:
        # Stable across restarts and overlapping deploys; never put BOT_TOKEN in the URL.
        secret = hmac.new(bot_token.encode(), b"telegram-webhook-v1", hashlib.sha256).hexdigest()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", secret):
        raise ValueError("WEBHOOK_SECRET: 1..256 ta harf, raqam, _ yoki - kerak")
    port = int(environ.get("PORT", "10000"))
    if not 1 <= port <= 65535:
        raise ValueError("PORT: 1..65535 oralig'ida bo'lishi kerak")
    return base_url + WEBHOOK_PATH, secret, port


def create_app(bot: Bot, dispatcher: Dispatcher, webhook_url: str, secret: str) -> web.Application:
    app = web.Application(client_max_size=1024 * 1024)

    async def health(request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "mode": "webhook"})

    async def register_webhook(bot: Bot) -> None:
        await bot.set_webhook(
            webhook_url, secret_token=secret,
            allowed_updates=dispatcher.resolve_used_update_types(),
            drop_pending_updates=False, request_timeout=20,
        )
        logging.info("Telegram webhook registered; HTTP server ready")

    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    dispatcher.startup.register(register_webhook)
    SimpleRequestHandler(
        dispatcher=dispatcher, bot=bot, secret_token=secret,
        handle_in_background=True,
    ).register(app, path=WEBHOOK_PATH)
    setup_application(app, dispatcher, bot=bot)
    # Keep the webhook registered on shutdown: Telegram must wake a sleeping service.
    return app
