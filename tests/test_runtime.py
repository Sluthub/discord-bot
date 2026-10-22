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

    async def test_housekeeping_consumes_native_member_iterator(self):
        role = Mock(id=91)
        retained = Mock(id=101, roles=[role])
        deleted = Mock(id=102, roles=[role], remove_roles=AsyncMock())
        unverified = Mock(id=103, roles=[])
        guild = Mock(fetch_roles=AsyncMock(return_value=[role]))

        async def members(*, limit):
            self.assertEqual(limit, 150)
            for member in (retained, deleted, unverified):
                yield member

        guild.fetch_members = Mock(side_effect=members)
        self.app.JELLYFIN_USERS = ["retained", "unverified"]
        self.app.KNOWN_USERS = {"retained": 101, "deleted": 102,
                                "unverified": 103, "left": 104}
        with patch.object(self.app, "VERIFY_CHANNEL", 90, create=True), \
             patch.object(self.app, "VERIFIED_ROLE", 91, create=True), \
             patch.object(self.app.bot, "fetch_channel", AsyncMock(return_value=Mock(guild=guild))), \
             patch.object(self.app, "save_known_users") as save:
            await self.app.clean_known_users()
        self.assertEqual(self.app.KNOWN_USERS, {"retained": 101})
        deleted.remove_roles.assert_awaited_once_with(role)
        save.assert_called_once_with()

    async def test_member_fetch_failure_preserves_known_users(self):
        role = Mock(id=91)
        guild = Mock(fetch_roles=AsyncMock(return_value=[role]))

        async def members(*, limit):
            yield Mock(id=101, roles=[role])
            raise RuntimeError("synthetic member fetch failure")

        guild.fetch_members = Mock(side_effect=members)
        original = {"retained": 101, "unseen": 102}
        self.app.JELLYFIN_USERS = ["retained", "unseen"]
        self.app.KNOWN_USERS = original.copy()
        with patch.object(self.app, "VERIFY_CHANNEL", 90, create=True), \
             patch.object(self.app, "VERIFIED_ROLE", 91, create=True), \
             patch.object(self.app.bot, "fetch_channel", AsyncMock(return_value=Mock(guild=guild))), \
             patch.object(self.app, "save_known_users") as save:
            with self.assertRaisesRegex(RuntimeError, "synthetic member fetch failure"):
                await self.app.clean_known_users()
        self.assertEqual(self.app.KNOWN_USERS, original)
        save.assert_not_called()

    async def test_user_refresh_keeps_existing_mapping(self):
        self.app.KNOWN_USERS = {"fixture": 123}
        with patch.object(self.app, "jellyfin_api", AsyncMock(return_value=[{"Name": "fixture"}])):
            await self.app.fetch_jellyfin_users()
        self.assertEqual(self.app.JELLYFIN_USERS, ["fixture"])
        self.assertEqual(self.app.KNOWN_USERS, {"fixture": 123})


if __name__ == "__main__":
    unittest.main()
