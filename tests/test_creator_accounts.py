from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from webapp import creator_store, jobs, server, templates_store, voice_store


class CreatorWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patches = [
            patch.object(creator_store, "STORE_PATH", self.root / "creator_accounts.json"),
            patch.object(creator_store, "DATA_DIR", self.root / "creator_data"),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def test_new_account_switches_and_isolates_voice_and_template_writes(self):
        default_count = len(voice_store.list_voices())
        account = creator_store.create({"name": "测试工作区"})
        self.assertEqual(creator_store.active_id(), account["id"])
        self.assertEqual(len(voice_store.list_voices(account_id=account["id"])), default_count)

        created_voice = voice_store.create({"name": "独立测试音色"}, account_id=account["id"])
        self.assertIsNone(voice_store.get(created_voice["id"]))
        self.assertIsNotNone(voice_store.get(created_voice["id"], account_id=account["id"]))

        created_tpl = templates_store.create_template({"name": "独立测试模板"}, account_id=account["id"])
        self.assertTrue(any(t["id"] == created_tpl["id"]
                            for t in templates_store.list_templates(account_id=account["id"])))
        self.assertFalse(any(t["id"] == created_tpl["id"]
                             for t in templates_store.list_templates()))
        creator_store.select("default")
        self.assertEqual(creator_store.active_id(), "default")

    def test_duplicate_name_is_rejected(self):
        creator_store.create({"name": "同名账号"})
        with self.assertRaises(ValueError):
            creator_store.create({"name": "同名账号"})

    def test_cloned_voice_delete_preserves_shared_default_audio(self):
        base = self.root / "app"
        audio_dir = base / "voice_lib" / "audio"
        audio_dir.mkdir(parents=True)
        shared_audio = audio_dir / "shared.wav"
        shared_audio.write_bytes(b"default-audio")
        (base / "voice_lib" / "manifest.json").write_text(json.dumps({"voices": [{
            "id": "shared", "name": "共享音色", "gender": "female", "age_range": "26-32",
            "style_tags": [], "prompt_audio": "voice_lib/audio/shared.wav",
        }]}), encoding="utf-8")
        (base / "templates.json").write_text('{"templates": []}', encoding="utf-8")
        with patch.object(creator_store, "BASE_DIR", base), \
                patch.object(creator_store, "DATA_DIR", base / "creator_data"), \
                patch.object(voice_store, "BASE_DIR", base):
            account = creator_store.create({"name": "删除隔离测试"})
            voice_store.delete("shared", remove_audio=True, account_id=account["id"])
            self.assertTrue(shared_audio.exists())
            self.assertIsNotNone(voice_store.get("shared"))

            voice_store.create({"id": "owned", "name": "独立音频"}, account_id=account["id"])
            rel = voice_store.save_audio("owned", b"owned-audio", account_id=account["id"])
            owned_audio = base / rel
            self.assertTrue(owned_audio.exists())
            voice_store.delete("owned", remove_audio=True, account_id=account["id"])
            self.assertFalse(owned_audio.exists())
            self.assertTrue(shared_audio.exists())


class JobOwnershipTests(unittest.TestCase):
    def test_legacy_jobs_belong_to_default_and_new_jobs_are_filtered(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs_dir = Path(tmp) / "jobs"
            jobs_dir.mkdir()
            for job_id, owner in (("20260101-010101-a", None), ("20260101-010102-b", "other")):
                path = jobs_dir / job_id
                path.mkdir()
                payload = {"job_id": job_id, "topic": job_id, "status": "done", "minutes": 5}
                if owner:
                    payload["creator_account_id"] = owner
                (path / "job.json").write_text(json.dumps(payload), encoding="utf-8")
            with patch.object(jobs, "JOBS_DIR", jobs_dir):
                self.assertEqual(len(jobs.list_jobs("default")), 1)
                self.assertEqual(len(jobs.list_jobs("other")), 1)
                self.assertIsNotNone(jobs.get_job("20260101-010101-a", "default"))
                self.assertIsNone(jobs.get_job("20260101-010101-a", "other"))


class CreatorApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.jobs_dir = self.root / "jobs"
        legacy = self.jobs_dir / "legacy-job"
        legacy.mkdir(parents=True)
        (legacy / "job.json").write_text(json.dumps({
            "job_id": "legacy-job", "topic": "历史任务", "status": "done", "minutes": 5,
        }), encoding="utf-8")
        self.patches = [
            patch.object(creator_store, "STORE_PATH", self.root / "creator_accounts.json"),
            patch.object(creator_store, "DATA_DIR", self.root / "creator_data"),
            patch.object(jobs, "JOBS_DIR", self.jobs_dir),
            patch.object(jobs, "TRACE_FILE", self.jobs_dir / "trace.jsonl"),
        ]
        for item in self.patches:
            item.start()
        self.client = TestClient(server.app)

    def tearDown(self):
        self.client.close()
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def test_create_select_and_direct_link_isolation_over_http(self):
        response = self.client.get("/api/creator-accounts")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["active_id"], "default")
        self.assertEqual(len(self.client.get("/api/jobs").json()), 1)

        response = self.client.post("/api/creator-accounts", json={"name": "接口验收账号"})
        self.assertEqual(response.status_code, 200)
        account_id = response.json()["account"]["id"]
        self.assertEqual(response.json()["active_id"], account_id)
        self.assertEqual(self.client.get("/api/jobs").json(), [])
        self.assertEqual(self.client.get("/api/jobs/legacy-job").status_code, 404)
        self.assertEqual(self.client.get("/job/legacy-job").status_code, 404)
        self.assertTrue(self.client.get("/api/voices").json())
        self.assertTrue(self.client.get("/api/templates").json())

        response = self.client.post("/api/creator-accounts/default/select")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.client.get("/api/jobs").json()), 1)
        self.assertEqual(self.client.get("/api/jobs/legacy-job").status_code, 200)
        self.client.post(f"/api/creator-accounts/{account_id}/select")
        self.assertEqual(self.client.get("/api/jobs").json(), [])


if __name__ == "__main__":
    unittest.main()
