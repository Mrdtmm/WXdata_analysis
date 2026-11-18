import hashlib
import sqlite3
import tempfile
import unittest
import wave
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, call, patch

from app import config, decrypt, keys, media, processes, scanner, vendor_key


class IsolatedMediaTestCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.account = self.root / "wxid_fixture_ab12"
        self.account.mkdir()
        self.cache = self.root / "cache"
        for owner, name, value in (
            (config, "CACHE_DIR", self.cache),
            (config, "KEYS_DIR", self.root / "keys"),
            (decrypt, "CACHE_DIR", self.cache),
            (vendor_key, "CACHE_DIR", self.cache),
            (media, "CACHE_DIR", self.cache),
            (keys, "KEYS_DIR", self.root / "keys"),
        ):
            self.enterContext(patch.object(owner, name, value))
        for owner, name in (
            (vendor_key.subprocess, "run"),
            (vendor_key.subprocess, "Popen"),
            (processes, "list_weixin_instances"),
            (processes, "_kernel32"),
            (scanner, "scan_roots"),
            (config, "load_config"),
        ):
            self.forbid(owner, name)

    def forbid(self, owner, name):
        blocked = self.enterContext(patch.object(
            owner, name, side_effect=AssertionError(f"Unexpected real dependency: {name}"),
        ))
        self.addCleanup(blocked.assert_not_called)
        return blocked

    def write_fixture(self, relative_path, payload=b"synthetic fixture, not a real database"):
        target = self.account / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        return target


class MediaKeyPreparationTests(IsolatedMediaTestCase):
    OPTIONAL_DATABASES = (
        "db_storage/message/message_resource.db",
        "db_storage/hardlink/hardlink.db",
        "db_storage/emoticon/emoticon.db",
    )
    EXCLUDED_DATABASES = (
        "db_storage/message/media_1_backup.db",
        "db_storage/message/media_1a.db",
        "db_storage/message/media_1.db.bak",
        "db_storage/message/media_.db",
        "db_storage/message/media_words.db",
        "db_storage/message/media_-1.db",
        "db_storage/message/nested/media_3.db",
        "db_storage/message/message_0.db",
        "db_storage/message/message_resource_backup.db",
        "db_storage/contact/contact.db",
        "db_storage/session/session.db",
        "db_storage/unrelated/media_4.db",
        "db_storage/hardlink/hardlink_backup.db",
        "db_storage/emoticon/emoticon_backup.db",
    )

    def setUp(self):
        super().setUp()
        self.extract = self.enterContext(patch.object(vendor_key, "_extract_keys"))
        self.validate = self.enterContext(patch.object(decrypt, "decrypt_snapshot"))
        for owner, name in (
            (vendor_key, "extract_contact_key"),
            (vendor_key, "extract_message_keys"),
            (decrypt, "snapshot_db"),
            (keys, "save_key"),
            (keys, "save_db_keys"),
            (keys, "load_db_keys"),
            (keys, "load_saved_key"),
        ):
            self.forbid(owner, name)

    def prepare_databases(self, relative_dbs):
        for database in relative_dbs:
            self.write_fixture(database)
        self.db_keys = {
            database: bytes([index + 1]) * 32
            for index, database in enumerate(relative_dbs)
        }
        self.reports = {
            database: {
                "ok": True,
                "pages": index + 1,
                "tables": [f"fixture_table_{index}"],
                "wal_present": False,
            }
            for index, database in enumerate(relative_dbs)
        }
        reports_by_name = {Path(database).name: report for database, report in self.reports.items()}
        self.extract.return_value = self.db_keys.copy()
        self.validate.side_effect = lambda snapshot, key: dict(reports_by_name[snapshot.name])

    def assert_prepared(self, result, expected_databases):
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        db_keys, reports = result
        self.assertEqual(db_keys, self.db_keys)
        self.assertIsInstance(reports, list)
        self.assertEqual(len(reports), len(expected_databases))
        self.assertEqual(
            {report["database"]: report for report in reports},
            {database: {**self.reports[database], "database": database} for database in expected_databases},
        )
        self.extract.assert_called_once()
        account, relative_dbs, scan_name = self.extract.call_args.args
        self.assertEqual(account, self.account)
        self.assertCountEqual(relative_dbs, expected_databases)
        self.assertIsInstance(scan_name, str)
        self.assertTrue(scan_name)
        self.assertNotIn(scan_name, {"vendor_scan_contact", "vendor_scan_messages"})
        self.assertCountEqual(self.validate.call_args_list, [
            call(self.cache / self.account.name / "raw" / Path(database).name, self.db_keys[database])
            for database in expected_databases
        ])

    def test_only_existing_numeric_shards_and_optional_whitelisted_databases_are_prepared(self):
        expected = (
            "db_storage/message/media_0.db",
            "db_storage/message/media_2.db",
            "db_storage/message/media_12.db",
            "db_storage/message/media_001.db",
        ) + self.OPTIONAL_DATABASES
        self.prepare_databases(expected)
        for database in self.EXCLUDED_DATABASES:
            self.write_fixture(database)
        (self.account / "db_storage/message/media_9.db").mkdir()
        sibling = self.root / "wxid_other_cd34" / "db_storage/message/media_99.db"
        sibling.parent.mkdir(parents=True)
        sibling.write_bytes(b"synthetic other-account database")

        result = vendor_key.extract_media_keys(self.account)

        self.assert_prepared(result, expected)

    def test_numeric_shard_can_be_prepared_without_optional_databases(self):
        expected = ("db_storage/message/media_10.db",)
        self.prepare_databases(expected)
        self.assert_prepared(vendor_key.extract_media_keys(self.account), expected)

    def test_each_optional_database_can_be_prepared_without_message_or_media_shards(self):
        for database in self.OPTIONAL_DATABASES:
            with self.subTest(database=database):
                self.extract.reset_mock()
                self.validate.reset_mock()
                self.prepare_databases((database,))
                try:
                    self.assert_prepared(vendor_key.extract_media_keys(self.account), (database,))
                finally:
                    (self.account / database).unlink()

    def test_empty_media_scope_errors_without_extraction_or_validation(self):
        for unrelated_files in (False, True):
            with self.subTest(unrelated_files=unrelated_files):
                if unrelated_files:
                    for database in self.EXCLUDED_DATABASES:
                        self.write_fixture(database)
                    (self.account / "db_storage/message/media_8.db").mkdir()
                    for database in self.OPTIONAL_DATABASES:
                        (self.account / database).mkdir()
                with self.assertRaises((RuntimeError, FileNotFoundError)):
                    vendor_key.extract_media_keys(self.account)
                self.extract.assert_not_called()
                self.validate.assert_not_called()

    def test_any_database_validation_failure_raises_instead_of_returning_partial_success(self):
        expected = ("db_storage/message/media_0.db", "db_storage/message/media_1.db") + self.OPTIONAL_DATABASES
        self.prepare_databases(expected)
        for failed_database in expected:
            with self.subTest(database=failed_database):
                self.extract.reset_mock()
                self.validate.reset_mock()
                failed_snapshot = self.cache / self.account.name / "raw" / Path(failed_database).name

                def validate(snapshot, key):
                    if snapshot == failed_snapshot:
                        return {"ok": False, "reason": "synthetic validation failure"}
                    return {"ok": True, "pages": 1}

                self.validate.side_effect = validate
                with self.assertRaises(RuntimeError):
                    vendor_key.extract_media_keys(self.account)
                self.extract.assert_called_once()
                self.validate.assert_any_call(failed_snapshot, self.db_keys[failed_database])

    def test_repeated_preparation_uses_a_unique_scan_name(self):
        self.prepare_databases(("db_storage/message/media_0.db",))
        vendor_key.extract_media_keys(self.account)
        vendor_key.extract_media_keys(self.account)
        self.assertEqual(self.extract.call_count, 2)
        scan_names = [invocation.args[2] for invocation in self.extract.call_args_list]
        for scan_name in scan_names:
            self.assertIsInstance(scan_name, str)
            self.assertTrue(scan_name)
            self.assertNotIn(scan_name, {"vendor_scan_contact", "vendor_scan_messages"})
        self.assertNotEqual(scan_names[0], scan_names[1])

    def test_extraction_failure_does_not_attempt_database_validation(self):
        self.prepare_databases(("db_storage/message/media_0.db",))
        self.extract.side_effect = RuntimeError("synthetic extraction failure")
        with self.assertRaises(RuntimeError):
            vendor_key.extract_media_keys(self.account)
        self.extract.assert_called_once()
        self.validate.assert_not_called()


class DatabaseKeyMergeTests(IsolatedMediaTestCase):
    def test_contact_message_and_media_keys_are_preserved_in_both_preparation_orders(self):
        messages = {
            "db_storage/contact/contact.db": bytes([1]) * 32,
            "db_storage/message/message_0.db": bytes([2]) * 32,
        }
        media_keys = {
            "db_storage/message/media_0.db": bytes([3]) * 32,
            "db_storage/emoticon/emoticon.db": bytes([4]) * 32,
        }
        for index, (first, second) in enumerate(((messages, media_keys), (media_keys, messages))):
            with self.subTest(order=index):
                account = f"wxid_merge_{index:04d}"
                keys.KEYS_DIR.mkdir(exist_ok=True)
                contact_file = keys.KEYS_DIR / f"{account}.json"
                contact_payload = b'{"key_hex": "synthetic contact entry must stay unchanged"}'
                contact_file.write_bytes(contact_payload)
                for subset in (first, second):
                    with patch.object(keys, "load_db_keys", wraps=keys.load_db_keys) as load:
                        keys.save_db_keys(account, subset)
                        load.assert_called_once_with(account)
                    self.assertEqual(contact_file.read_bytes(), contact_payload)
                self.assertEqual(keys.load_db_keys(account), {**messages, **media_keys})

    def test_preparing_another_media_subset_preserves_existing_keys_and_updates_matching_key(self):
        account = self.account.name
        original = {
            "db_storage/contact/contact.db": bytes([1]) * 32,
            "db_storage/message/message_0.db": bytes([2]) * 32,
            "db_storage/message/media_0.db": bytes([3]) * 32,
            "db_storage/message/media_1.db": bytes([4]) * 32,
        }
        update = {
            "db_storage/message/media_0.db": bytes([5]) * 32,
            "db_storage/hardlink/hardlink.db": bytes([6]) * 32,
        }
        keys.save_db_keys(account, original)
        keys.save_db_keys(account, update)
        self.assertEqual(keys.load_db_keys(account), {**original, **update})


class OtherMediaStatusTests(IsolatedMediaTestCase):
    def setUp(self):
        super().setUp()
        self.timestamp = int(datetime(2026, 11, 7, 12, tzinfo=timezone.utc).timestamp())
        self.month = datetime.fromtimestamp(self.timestamp, timezone.utc).astimezone().strftime("%Y-%m")
        self.md5 = "0123456789abcdef0123456789abcdef"
        self.check_cancel = Mock()

    def assert_status(self, account, message, media_type, status, reason=None, text=None):
        self.check_cancel.reset_mock()
        stats = media.mark_other_media(account, [message], media_type, self.check_cancel)
        self.check_cancel.assert_called_once_with()
        self.assertEqual(stats, {
            f"{media_type}_messages": 1,
            "exported": 0,
            "missing": int(status == "missing"),
            "failed": 0,
            "unverified": int(status == "unverified"),
            "unique_files": 0,
            "bytes": 0,
        })
        self.assertEqual(message["media_status"], status)
        self.assertIsNone(message.get("media_path"))
        self.assertIsNone(message.get("media_tier"))
        if reason is not None:
            self.assertEqual(message["media_reason"], reason)
        if text is not None:
            self.assertIn(text, message["content"])
        if status == "unverified":
            self.assertNotIn("未找到本地文件", message["content"])
            self.assertNotIn("找到本地文件但无法还原", message["content"])

    def assert_voice_unverified_without_file_association(self):
        for media_id in (None, self.md5):
            with self.subTest(media_id=media_id):
                message = {
                    "message_type": "voice", "base_type": 34, "content": "[语音]",
                    "timestamp": self.timestamp, "media_id": media_id,
                }
                with (
                    patch.object(Path, "rglob", side_effect=AssertionError("Do not scan arbitrary voice files")),
                    patch.object(Path, "glob", side_effect=AssertionError("Do not scan arbitrary voice files")),
                    patch.object(Path, "iterdir", side_effect=AssertionError("Do not scan arbitrary voice files")),
                ):
                    self.assert_status(
                        self.account, message, "voice", "unverified",
                        "voice_mapping_unverified", "语音载荷尚未定位，不能判定本地缺失",
                    )

    def test_voice_with_unrelated_month_file_is_unverified_not_associated_or_missing(self):
        self.write_fixture(f"msg/voice/{self.month}/unrelated/another-message.silk", b"unrelated voice fixture")
        self.assert_voice_unverified_without_file_association()

    def test_voice_without_any_local_files_is_unverified_not_missing(self):
        self.assert_voice_unverified_without_file_association()

    def test_known_emoji_candidate_with_unknown_blob_is_only_unverified(self):
        candidates = (
            f"cache/{self.month}/Emoticon/{self.md5[:2]}/{self.md5}",
            f"business/emoticon/Persist/{self.md5[:2]}/{self.md5}",
            f"business/emoticon/Thumb/{self.md5[:2]}/{self.md5}.thumb",
        )
        for candidate in candidates:
            for identifier in ("emoji_md5", "media_id"):
                with self.subTest(candidate=candidate, identifier=identifier):
                    source = self.write_fixture(candidate, b"opaque synthetic blob, not a validated image")
                    try:
                        message = {
                            "message_type": "emoji", "base_type": 47, "content": "[表情]",
                            "timestamp": self.timestamp, identifier: self.md5,
                        }
                        self.assert_status(
                            self.account, message, "emoji", "unverified",
                            "emoji_candidate_unverified", "找到表情候选，尚未验证能否还原",
                        )
                    finally:
                        source.unlink()

    def test_emoji_without_known_candidate_remains_missing(self):
        for identifier in (self.md5, None, "not-an-md5"):
            with self.subTest(identifier=identifier):
                message = {
                    "message_type": "emoji", "base_type": 47, "content": "[表情]",
                    "timestamp": self.timestamp, "emoji_md5": identifier,
                }
                self.assert_status(self.account, message, "emoji", "missing", text="未找到本地文件")

    def test_unrelated_emoji_blob_is_not_a_candidate(self):
        unrelated_md5 = "abcdef0123456789abcdef0123456789"
        self.write_fixture(f"business/emoticon/Persist/{unrelated_md5[:2]}/{unrelated_md5}")
        message = {
            "message_type": "emoji", "base_type": 47, "content": "[表情]",
            "timestamp": self.timestamp, "emoji_md5": self.md5,
        }
        self.assert_status(self.account, message, "emoji", "missing", text="未找到本地文件")


class VoicePayloadExportTests(IsolatedMediaTestCase):
    def setUp(self):
        super().setUp()
        self.timestamp = 1735901014
        self.plain = self.cache / self.account.name / "plain"
        self.plain.mkdir(parents=True)
        self.conversation_dir = self.root / "output" / "conversation"
        self.conversation_dir.mkdir(parents=True)
        self.check_cancel = Mock()
        self.payload = b"\x02#!SILK_V3synthetic voice payload"
        self._create_media_db()

    def _create_media_db(self):
        path = self.plain / "media_0.sqlite"
        conn = sqlite3.connect(path)
        try:
            conn.execute("CREATE TABLE Name2Id(user_name TEXT PRIMARY KEY)")
            conn.execute(
                "CREATE TABLE VoiceInfo(chat_name_id INTEGER, create_time INTEGER, "
                "local_id INTEGER, svr_id INTEGER, voice_data BLOB, data_index TEXT DEFAULT '0')"
            )
            conn.execute("INSERT INTO Name2Id(rowid, user_name) VALUES (1, 'friend')")
            conn.execute(
                "INSERT INTO VoiceInfo VALUES (1, ?, 10, 999, ?, '0')",
                (self.timestamp, self.payload),
            )
            conn.commit()
        finally:
            conn.close()

    def _message(self, local_id=10, server_id=999):
        return {
            "message_type": "voice",
            "base_type": 34,
            "content": "[语音]",
            "conversation": "friend",
            "conversation_name": "Fixture Friend",
            "local_id": local_id,
            "server_id": server_id,
            "timestamp": self.timestamp,
            "message_uid": f"message_0:Msg_fixture:{local_id}",
        }

    def test_matched_voice_falls_back_to_silk_when_decode_fails(self):
        from app import exporter

        store = media.VoiceStore(self.plain)
        self.addCleanup(store.close)
        message = self._message()
        with patch.object(media, "_pilk") as mock_pilk:
            mock_pilk.silk_to_wav.side_effect = RuntimeError("synthetic decode failure")
            stats = media.export_voices(
                self.account, [message], self.conversation_dir, self.check_cancel, store,
            )
        self.assertEqual(stats["exported"], 1)
        self.assertEqual(stats["unverified"], 0)
        self.assertEqual(stats["bytes"], len(self.payload))
        self.assertEqual(message["media_status"], "exported")
        self.assertEqual(message["media_tier"], "silk_raw")
        self.assertEqual(message["media_reason"], "voice_silk_decoder_missing")
        target = self.conversation_dir / message["media_path"]
        self.assertEqual(target.read_bytes(), self.payload)
        self.assertIn("尚未解码播放", message["content"])
        html = exporter._render_html([message], False, False, True)
        self.assertIn("下载语音原始载荷（SILK）", html)
        self.assertIn("浏览器暂不能直接播放", html)

    def test_matched_voice_decodes_to_wav(self):
        from app import exporter

        store = media.VoiceStore(self.plain)
        self.addCleanup(store.close)
        message = self._message()

        def fake_silk_to_wav(silk, wav, rate=24000):
            with wave.open(wav, "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(rate)
                handle.writeframes(b"\x00\x00" * 2400)

        with patch.object(media, "_pilk") as mock_pilk:
            mock_pilk.silk_to_wav.side_effect = fake_silk_to_wav
            stats = media.export_voices(
                self.account, [message], self.conversation_dir, self.check_cancel, store,
            )
        self.assertEqual(stats["exported"], 1)
        self.assertEqual(message["media_status"], "exported")
        self.assertEqual(message["media_tier"], "wav")
        self.assertNotIn("media_reason", message)
        target = self.conversation_dir / message["media_path"]
        self.assertEqual(target.suffix, ".wav")
        self.assertFalse(target.with_suffix(".silk").exists())
        with wave.open(str(target), "rb") as handle:
            self.assertEqual(handle.getnchannels(), 1)
            self.assertEqual(handle.getframerate(), 24000)
            self.assertGreater(handle.getnframes(), 0)
        self.assertEqual(stats["bytes"], target.stat().st_size)
        self.assertIn("WAV", message["content"])
        html = exporter._render_html([message], False, False, True)
        self.assertIn("media-audio", html)
        self.assertIn('"media_tier": "wav"', html)

    def test_voice_exports_silk_when_pilk_unavailable(self):
        store = media.VoiceStore(self.plain)
        self.addCleanup(store.close)
        message = self._message()
        with patch.object(media, "_pilk", None):
            stats = media.export_voices(
                self.account, [message], self.conversation_dir, self.check_cancel, store,
            )
        self.assertEqual(stats["exported"], 1)
        self.assertEqual(message["media_tier"], "silk_raw")
        self.assertEqual(message["media_reason"], "voice_silk_decoder_missing")
        target = self.conversation_dir / message["media_path"]
        self.assertEqual(target.suffix, ".silk")
        self.assertEqual(target.read_bytes(), self.payload)

    def test_unmatched_voice_stays_unverified_without_file(self):
        store = media.VoiceStore(self.plain)
        self.addCleanup(store.close)
        message = self._message(local_id=11, server_id=1000)
        stats = media.export_voices(
            self.account, [message], self.conversation_dir, self.check_cancel, store,
        )
        self.assertEqual(stats["exported"], 0)
        self.assertEqual(stats["unverified"], 1)
        self.assertEqual(message["media_reason"], "voice_mapping_unverified")
        self.assertIsNone(message.get("media_path"))
        self.assertNotIn("未找到本地文件", message["content"])

    def test_missing_media_db_marks_voice_unverified_not_missing(self):
        empty_plain = self.cache / "empty" / "plain"
        empty_plain.mkdir(parents=True)
        store = media.VoiceStore(empty_plain)
        self.addCleanup(store.close)
        self.assertFalse(store.available)
        message = self._message()
        stats = media.export_voices(
            self.account, [message], self.conversation_dir, self.check_cancel, store,
        )
        self.assertEqual(stats["unverified"], 1)
        self.assertEqual(message["media_reason"], "voice_media_db_unavailable")
        self.assertNotIn("未找到本地文件", message["content"])

    def test_collect_messages_ignores_message_resource_sqlite(self):
        from app import messages

        username = "friend"
        table = "Msg_" + hashlib.md5(username.encode("utf-8")).hexdigest()
        conn = sqlite3.connect(self.plain / "message_0.sqlite")
        try:
            conn.execute("CREATE TABLE Name2Id(user_name TEXT PRIMARY KEY)")
            conn.execute("INSERT INTO Name2Id(rowid, user_name) VALUES (1, ?)", (username,))
            conn.execute(
                f'CREATE TABLE "{table}" (local_id INTEGER, server_id INTEGER, local_type INTEGER, '
                "sort_seq INTEGER, real_sender_id INTEGER, create_time INTEGER, source BLOB, "
                "message_content BLOB, packed_info_data BLOB, WCDB_CT_source INTEGER, "
                "WCDB_CT_message_content INTEGER)"
            )
            conn.execute(
                f'INSERT INTO "{table}" VALUES (10, 999, 34, 1, 1, ?, NULL, ?, NULL, 0, 0)',
                (self.timestamp, "[语音]"),
            )
            conn.commit()
        finally:
            conn.close()
        resource = sqlite3.connect(self.plain / "message_resource.sqlite")
        try:
            resource.execute("CREATE TABLE MessageResourceInfo(message_id INTEGER)")
            resource.commit()
        finally:
            resource.close()
        with patch.object(messages, "CACHE_DIR", self.cache):
            collected, warnings = messages.collect_messages(
                self.account.name, [{"username": username, "nickname": "Friend"}],
                None, None, lambda n: None, lambda: False,
            )
        self.assertEqual(len(collected[username]), 1)
        self.assertEqual(collected[username][0]["message_type"], "voice")

    def test_prepare_voice_store_only_decrypts_numeric_media_shards(self):
        source = self.write_fixture("db_storage/message/media_0.db")
        self.write_fixture("db_storage/contact/contact.db")
        snapshot = self.cache / self.account.name / "raw" / "media_0.db"
        with (
            patch.object(keys, "load_db_keys", return_value={
                "db_storage/message/media_0.db": bytes([1]) * 32,
                "db_storage/contact/contact.db": bytes([2]) * 32,
            }),
            patch.object(decrypt, "snapshot_db", return_value=snapshot) as snap,
            patch.object(decrypt, "decrypt_snapshot", return_value={"ok": True}) as validate,
        ):
            store = media.prepare_voice_store(self.account.name, self.account)
            self.addCleanup(store.close)
        snap.assert_called_once_with(self.account, "db_storage/message/media_0.db")
        validate.assert_called_once_with(snapshot, bytes([1]) * 32)
        self.assertTrue(store.available)


if __name__ == "__main__":
    unittest.main()
