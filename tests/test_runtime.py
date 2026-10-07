import asyncio
import importlib.util
import pathlib
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

SOURCE = pathlib.Path(__file__).resolve().parents[1] / "main.py"


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        specification = importlib.util.spec_from_file_location("sluthub_bot_test", SOURCE)
        cls.app = importlib.util.module_from_spec(specification)
        original_read = pathlib.Path.read_bytes

        def read(path):
            return b"{}" if path.name == "known_users.json" else original_read(path)

        with tempfile.TemporaryDirectory() as temporary:
            import os

            previous = os.getcwd()
            try:
                os.chdir(temporary)
                with patch.object(pathlib.Path, "read_bytes", read):
                    specification.loader.exec_module(cls.app)
            finally:
                os.chdir(previous)
        cls.app.JELLYFIN_API = "https://media.invalid"
        cls.app.JELLYFIN_APIKEY = "synthetic-test-key"

    async def test_registered_command_and_existing_intents(self):
        self.assertTrue(self.app.bot.intents.members)
        self.assertTrue(self.app.bot.intents.message_content)
        self.app.bot.add_all_application_commands()
        self.assertIn(self.app.gib_ip, self.app.bot.get_all_application_commands())

    async def test_modern_auth_and_request_parameters(self):
        response = AsyncMock()
        response.raise_for_status = Mock()
        response.json.return_value = {"Items": []}
        request = AsyncMock()
        request.__aenter__.return_value = response
        session = Mock()
        session.request.return_value = request
        context = AsyncMock()
        context.__aenter__.return_value = session
        with patch.object(self.app.aiohttp, "ClientSession", return_value=context):
            result = await self.app.jellyfin_api("GET", "/Items", params={"limit": 1})
        self.assertEqual(result, {"Items": []})
        self.assertEqual(session.request.call_args.kwargs["headers"]["Authorization"],
                         'MediaBrowser Token="synthetic-test-key"')
        self.assertEqual(session.request.call_args.kwargs["params"], {"limit": 1})
        response.raise_for_status.assert_called_once()

    async def test_http_failure_does_not_parse_success_data(self):
        response = AsyncMock()
        response.raise_for_status = Mock(side_effect=RuntimeError("synthetic HTTP failure"))
        request = AsyncMock()
        request.__aenter__.return_value = response
        session = Mock()
        session.request.return_value = request
        context = AsyncMock()
        context.__aenter__.return_value = session
        with patch.object(self.app.aiohttp, "ClientSession", return_value=context):
            with self.assertRaises(RuntimeError):
                await self.app.jellyfin_api("GET", "/Users")
        response.json.assert_not_awaited()

    async def test_user_refresh_keeps_existing_mapping(self):
        self.app.KNOWN_USERS = {"fixture": 123}
        with patch.object(self.app, "jellyfin_api", AsyncMock(return_value=[{"Name": "fixture"}])):
            await self.app.fetch_jellyfin_users()
        self.assertEqual(self.app.JELLYFIN_USERS, ["fixture"])
        self.assertEqual(self.app.KNOWN_USERS, {"fixture": 123})


if __name__ == "__main__":
    unittest.main()
