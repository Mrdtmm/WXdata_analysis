import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import config, main


class MemoryExtractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.accounts = ["wxid_alpha_ab12", "wxid_beta_cd34"]
        for account in self.accounts:
            (self.root / account).mkdir()
        self.key = bytes(range(32))
        self.db_keys = {
            "db_storage/contact/contact.db": self.key,
            "db_storage/message/message_0.db": bytes(reversed(range(32))),
        }
        self.instances = self.enterContext(patch.object(
            main.processes, "list_weixin_instances", return_value=[{"root_pid": 123}],
        ))
        self.contact = self.enterContext(patch.object(
            main.vendor_key, "extract_contact_key", return_value=self.key,
        ))
        self.messages = self.enterContext(patch.object(
            main.vendor_key, "extract_message_keys", return_value=self.db_keys,
        ))
        self.media_db_keys = {
            "db_storage/message/media_0.db": bytes([17]) * 32,
            "db_storage/emoticon/emoticon.db": bytes([34]) * 32,
        }
        self.media_reports = [
            {"ok": True, "database": database, "pages": index + 1}
            for index, database in enumerate(self.media_db_keys)
        ]
        self.media = self.enterContext(patch.object(
            main.vendor_key, "extract_media_keys",
            return_value=(self.media_db_keys, self.media_reports),
        ))
        self.validate = self.enterContext(patch.object(
            main, "_validate_and_save", side_effect=lambda *args: {"ok": True},
        ))
        self.save = self.enterContext(patch.object(main.keys, "save_db_keys"))
        self.save_contact = self.enterContext(patch.object(main.keys, "save_key"))

        async def call_in_place(function, *args, **kwargs):
            return function(*args, **kwargs)

        self.threadpool = self.enterContext(patch.object(
            main, "run_in_threadpool", new_callable=AsyncMock, side_effect=call_in_place,
        ))
        for owner, name in (
            (main.vendor_key, "_extract_keys"),
            (main.vendor_key.subprocess, "run"),
            (main.vendor_key.subprocess, "Popen"),
            (main.processes, "_kernel32"),
            (main.keys, "load_db_keys"),
            (main.keys, "load_saved_key"),
            (main.decrypt, "snapshot_db"),
            (main.decrypt, "decrypt_snapshot"),
        ):
            blocked = self.enterContext(patch.object(
                owner, name, side_effect=AssertionError(f"Unexpected real dependency: {name}"),
            ))
            self.addCleanup(blocked.assert_not_called)
        self.enterContext(patch.object(
            main, "load_config", side_effect=AssertionError("Extraction must not load an account list"),
        ))

    def request(self, **overrides):
        values = {
            "root": str(self.root),
            "account_dir": self.accounts[0],
            "offline_confirmed": True,
            "risk_confirmed": True,
            "scope": "contact",
        }
        values.update(overrides)
        return main.MemoryKeyRequest(**values)

    def assert_no_extraction(self):
        self.contact.assert_not_called()
        self.messages.assert_not_called()
        self.media.assert_not_called()
        self.threadpool.assert_not_awaited()
        self.validate.assert_not_called()
        self.save.assert_not_called()
        self.save_contact.assert_not_called()

    async def test_any_selected_account_can_extract_both_scopes(self):
        for account in self.accounts:
            for scope in ("contact", "messages"):
                with self.subTest(account=account, scope=scope):
                    for mock in (self.contact, self.messages, self.validate, self.save):
                        mock.reset_mock()
                    report = await main.memory_extract(self.request(account_dir=account, scope=scope))
                    self.assertTrue(report["ok"])
                    self.assertEqual(report["key_hex"], self.key.hex())
                    account_path = (self.root / account).resolve()
                    self.media.assert_not_called()
                    self.save_contact.assert_not_called()
                    self.validate.assert_called_once_with(account_path, account, self.key)
                    if scope == "contact":
                        self.contact.assert_called_once_with(account_path)
                        self.messages.assert_not_called()
                        self.save.assert_not_called()
                    else:
                        self.messages.assert_called_once_with(account_path)
                        self.contact.assert_not_called()
                        self.save.assert_called_once_with(account, self.db_keys)
                        self.assertEqual(report["message_key_count"], 1)

    async def test_both_confirmations_are_required(self):
        for offline, risk in ((False, False), (True, False), (False, True)):
            with self.subTest(offline=offline, risk=risk):
                report = await main.memory_extract(self.request(
                    offline_confirmed=offline, risk_confirmed=risk,
                ))
                self.assertFalse(report["ok"])
                self.assertIn("必须先确认", report["reason"])
        self.instances.assert_not_called()
        self.assert_no_extraction()

    async def test_zero_and_multiple_instances_are_rejected(self):
        for scope in ("contact", "messages", "media"):
            for count in (0, 2):
                with self.subTest(scope=scope, count=count):
                    self.instances.return_value = [{"root_pid": index} for index in range(count)]
                    report = await main.memory_extract(self.request(
                        scope=scope, media_confirmed=True,
                    ))
                    self.assertFalse(report["ok"])
                    self.assertIn("所选账号", report["reason"])
        self.assert_no_extraction()

    async def test_invalid_or_missing_account_paths_are_rejected(self):
        (self.root / "invalid-account").mkdir()
        (self.root / "wxid_file_ab12").write_bytes(b"not an account directory")
        for scope in ("contact", "messages", "media"):
            for account in (
                "../wxid_outside_ab12", "invalid-account", "wxid_missing_ab12", "wxid_file_ab12",
            ):
                with self.subTest(scope=scope, account=account):
                    report = await main.memory_extract(self.request(
                        account_dir=account, scope=scope, media_confirmed=True,
                    ))
                    self.assertFalse(report["ok"])
        self.instances.assert_not_called()
        self.assert_no_extraction()

    async def test_unknown_scope_is_rejected(self):
        report = await main.memory_extract(self.request(scope="unknown"))
        self.assertFalse(report["ok"])
        self.assertEqual(report["reason"], "未知的密钥提取范围")
        self.assert_no_extraction()

    async def test_media_consent_defaults_to_false_and_rejects_before_enumeration(self):
        for confirmation in ({}, {"media_confirmed": False}):
            with self.subTest(confirmation=confirmation):
                request = self.request(scope="media", **confirmation)
                self.assertFalse(request.media_confirmed)
                report = await main.memory_extract(request)
                self.assertFalse(report["ok"])
                self.assertTrue(report["reason"])
                self.assertNotIn("key_hex", report)
                self.instances.assert_not_called()
                self.assert_no_extraction()

    async def test_media_still_requires_offline_and_risk_consent(self):
        for offline, risk in ((False, False), (True, False), (False, True)):
            with self.subTest(offline=offline, risk=risk):
                report = await main.memory_extract(self.request(
                    scope="media", media_confirmed=True,
                    offline_confirmed=offline, risk_confirmed=risk,
                ))
                self.assertFalse(report["ok"])
                self.assertIn("必须先确认", report["reason"])
                self.instances.assert_not_called()
                self.assert_no_extraction()

    async def test_media_process_enumeration_error_does_not_extract_or_save(self):
        self.instances.side_effect = OSError("模拟进程枚举失败")
        report = await main.memory_extract(self.request(scope="media", media_confirmed=True))
        self.assertFalse(report["ok"])
        self.assertIn("模拟进程枚举失败", report["reason"])
        self.assert_no_extraction()

    async def test_media_success_only_saves_database_keys_and_returns_validation_reports(self):
        for account in self.accounts:
            with self.subTest(account=account):
                for mock in (self.instances, self.media, self.threadpool, self.save):
                    mock.reset_mock()
                report = await main.memory_extract(self.request(
                    account_dir=account, scope="media", media_confirmed=True,
                ))
                account_path = (self.root / account).resolve()
                self.instances.assert_called_once_with()
                self.threadpool.assert_awaited_once_with(self.media, account_path)
                self.media.assert_called_once_with(account_path)
                self.save.assert_called_once_with(account, self.media_db_keys)
                self.assertEqual(report, {
                    "ok": True,
                    "media_key_count": len(self.media_db_keys),
                    "databases": self.media_reports,
                })
                self.assertNotIn("key_hex", repr(report))
                for key in (*self.db_keys.values(), *self.media_db_keys.values()):
                    self.assertNotIn(key.hex(), repr(report))
                self.contact.assert_not_called()
                self.messages.assert_not_called()
                self.validate.assert_not_called()
                self.save_contact.assert_not_called()

    async def test_media_extraction_or_validation_failure_never_saves_or_exposes_keys(self):
        for error_type in (FileNotFoundError, RuntimeError, OSError):
            with self.subTest(error_type=error_type):
                self.media.reset_mock()
                self.threadpool.reset_mock()
                self.media.side_effect = error_type("模拟媒体库提取或校验失败")
                report = await main.memory_extract(self.request(
                    scope="media", media_confirmed=True,
                ))
                self.assertEqual(report, {"ok": False, "reason": "模拟媒体库提取或校验失败"})
                account_path = (self.root / self.accounts[0]).resolve()
                self.threadpool.assert_awaited_once_with(self.media, account_path)
                self.media.assert_called_once_with(account_path)
                self.assertNotIn("key_hex", report)
                self.contact.assert_not_called()
                self.messages.assert_not_called()
                self.validate.assert_not_called()
                self.save.assert_not_called()
                self.save_contact.assert_not_called()

    async def test_validation_failure_never_exposes_or_saves_keys(self):
        self.validate.side_effect = None
        self.validate.return_value = {"ok": False, "reason": "模拟校验失败"}
        report = await main.memory_extract(self.request(scope="messages"))
        self.assertFalse(report["ok"])
        self.assertNotIn("key_hex", report)
        self.save.assert_not_called()

    async def test_extraction_failure_does_not_validate_or_save(self):
        self.contact.side_effect = RuntimeError("模拟提取失败")
        report = await main.memory_extract(self.request())
        self.assertFalse(report["ok"])
        self.assertEqual(report["reason"], "模拟提取失败")
        self.validate.assert_not_called()
        self.save.assert_not_called()


class ConfigTests(unittest.TestCase):
    def test_no_account_list_in_default_or_legacy_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            with patch.object(config, "CONFIG_PATH", path):
                self.assertNotIn("memory_key", config.load_config())
                for accounts in ("[]", "[wxid_alpha_ab12]"):
                    path.write_text(
                        f"memory_key:\n  allowed_accounts: {accounts}\n"
                        "server:\n  host: 0.0.0.0\n  port: 9876\n",
                        encoding="utf-8",
                    )
                    loaded = config.load_config()
                    self.assertNotIn("memory_key", loaded)
                    self.assertEqual(loaded["server"]["host"], "127.0.0.1")
                    self.assertEqual(loaded["server"]["port"], 9876)


if __name__ == "__main__":
    unittest.main()
