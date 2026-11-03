import unittest
from unittest.mock import AsyncMock

from aiogram import Bot, Dispatcher
from web_server import WEBHOOK_PATH, create_app, webhook_settings


class WebhookTests(unittest.TestCase):
    def test_render_defaults_and_stable_secret(self):
        env = {"RENDER_EXTERNAL_URL": "https://example.onrender.com/", "PORT": "12345"}
        url, secret, port = webhook_settings(env, "test-token")
        self.assertEqual(url, "https://example.onrender.com" + WEBHOOK_PATH)
        self.assertEqual(port, 12345)
        self.assertEqual(secret, webhook_settings(env, "test-token")[1])
        self.assertNotEqual(secret, webhook_settings(env, "different-token")[1])

    def test_bad_settings_rejected(self):
        for value in ["http://example.com", "https://example.com/path", "https://user:pass@example.com"]:
            with self.assertRaises(ValueError):
                webhook_settings({"WEBHOOK_BASE_URL": value}, "test")
        with self.assertRaises(ValueError):
            webhook_settings({"RENDER_EXTERNAL_URL": "https://example.com", "WEBHOOK_SECRET": "has spaces"}, "test")

    def test_routes_and_webhook_registration(self):
        bot = Bot("123456:TEST_TOKEN_FOR_OFFLINE_TESTS")
        bot.set_webhook = AsyncMock(return_value=True)
        dispatcher = Dispatcher()
        app = create_app(bot, dispatcher, "https://example.com" + WEBHOOK_PATH, "test-secret")
        routes = {(route.method, route.resource.canonical) for route in app.router.routes()}
        self.assertIn(("GET", "/health"), routes)
        self.assertIn(("POST", WEBHOOK_PATH), routes)
        # This callback only awaits an immediate mock; no sockets/event loop required.
        callback = dispatcher.startup.handlers[-1].callback
        coroutine = callback(bot)
        with self.assertRaises(StopIteration):
            coroutine.send(None)
        bot.set_webhook.assert_awaited_once_with(
            "https://example.com" + WEBHOOK_PATH, secret_token="test-secret",
            allowed_updates=[], drop_pending_updates=False, request_timeout=20,
        )


if __name__ == "__main__":
    unittest.main()
