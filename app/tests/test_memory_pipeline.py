import json
import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

import memory_pipeline  # noqa: E402
import memory_client  # noqa: E402


class FakeMemoryClient:
    def __init__(self):
        self.payload = None

    def maintenance_snapshot(self):
        return {"snapshotRevision": "rev-1", "pendingEpisodes": [], "l1Memories": []}

    def policy(self):
        return {"instructions": "长期偏好进入 L1；L2 仅汇总 L1。"}

    def capture(self, payload):
        self.payload = payload
        return {
            "status": "stored", "episodeId": "ep-1",
            "writtenIds": ["m-1"], "profilesWritten": 1,
            "dirtyProfileKeys": [{"scope": "default", "dimension": "expression"}],
        }


class MemoryPipelineTest(unittest.TestCase):
    def test_memory_client_forget_maps_worker_payload(self):
        client = memory_client.ResearchReportMemoryClient.__new__(
            memory_client.ResearchReportMemoryClient
        )
        calls = []
        client.call = lambda method, payload: calls.append((method, payload)) or {"status": "ok"}
        client.forget(memory_id="m-1", include_episodes=True)
        self.assertEqual(
            [("forget", {"includeEpisodes": True, "id": "m-1"})], calls
        )

    def test_memory_users_have_isolated_directories(self):
        local = memory_client.ResearchReportMemoryClient(user="local")
        sijing = memory_client.ResearchReportMemoryClient(user="sijing")
        self.assertEqual("local", local.data_dir.name)
        self.assertEqual("sijing", sijing.data_dir.name)
        self.assertNotEqual(local.data_dir, sijing.data_dir)
        with self.assertRaises(memory_client.MemoryClientError):
            memory_client.ResearchReportMemoryClient(user="unknown")

    def test_pending_choice_writes_writing_episode_without_llm(self):
        client = FakeMemoryClient()
        pipeline = memory_pipeline.MemoryPipeline(client)
        result = pipeline.store_pending(
            {"feedback_id": "f1", "content": "这一类报告以后都写短段"},
            {
                "external_source_id": "openharness:s1:f1",
                "session_id": "s1", "task": "总裁报告",
                "topic": "case-1", "context_before": "原报告段落",
            },
            {"llm_backend": "codex", "llm_model": "gpt-5.6-sol"},
        )
        self.assertEqual("pending", result["decision"])
        self.assertEqual("pending", client.payload["decision"])
        self.assertEqual("s1", client.payload["episode"]["sessionId"])
        self.assertEqual("原报告段落", client.payload["episode"]["contextBefore"])

    def test_one_model_plan_writes_l0_l1_l2(self):
        client = FakeMemoryClient()
        pipeline = memory_pipeline.MemoryPipeline(client)
        result = pipeline.process(
            {"feedback_id": "f1", "content": "以后报告都使用短段"},
            {
                "external_source_id": "openharness:s1:f1",
                "session_id": "s1", "task": "总裁报告",
            },
            {"llm_backend": "codex", "llm_model": "gpt-5.6-sol"},
            call_model=lambda *args, **kwargs: json.dumps({
                "decision": "store",
                "episode": {},
                "memories": [{
                    "operationRef": "new1", "action": "store",
                    "scope": "default", "dimension": "expression",
                    "rule": "报告使用短段。",
                }],
                "profiles": [{
                    "scope": "default", "dimension": "expression",
                    "summary": "偏好短段。", "rules": ["报告使用短段。"],
                    "sourceRefs": ["new:new1"],
                }],
            }, ensure_ascii=False),
        )
        self.assertEqual("stored", result["status"])
        self.assertIs(client.payload["trustedWritingFeedback"], True)
        self.assertEqual("openharness:s1:f1", client.payload["episode"]["externalSourceId"])
        self.assertEqual("rev-1", client.payload["snapshotRevision"])
        self.assertEqual("new:new1", client.payload["profiles"][0]["sourceRefs"][0])

    def test_stable_preference_choice_forces_l1_and_l2(self):
        client = FakeMemoryClient()
        prompts = []
        result = memory_pipeline.MemoryPipeline(client).process(
            {"feedback_id": "f1", "content": "以后报告都使用短段"},
            {"external_source_id": "openharness:s1:f1", "session_id": "s1"},
            {"llm_backend": "codex", "llm_model": "gpt-5.6-sol"},
            call_model=lambda prompt, **kwargs: prompts.append(prompt) or json.dumps({
                "decision": "store", "episode": {},
                "memories": [{"operationRef": "new1", "action": "store", "scope": "default", "dimension": "expression", "rule": "报告使用短段。"}],
                "profiles": [{"scope": "default", "dimension": "expression", "summary": "短段偏好", "rules": ["报告使用短段。"], "sourceRefs": ["new:new1"]}],
            }, ensure_ascii=False),
            forced_decision="store",
        )
        self.assertEqual("store", result["decision"])
        self.assertIn("用户已明确选择保存为稳定偏好", prompts[0])


if __name__ == "__main__":
    unittest.main()
