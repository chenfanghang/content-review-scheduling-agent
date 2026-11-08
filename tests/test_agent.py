"""验证排期行为与关键业务边界。"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from agentscope.message import TextBlock, ToolCallBlock
from agentscope.model import ChatResponse

from src.composition import Tools, run
from src.domain.scheduling import ToolError
from src.infrastructure.models import MockModel, OpenCodeModel


class AgentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = str(Path(self.temp.name) / "drafts.db")
        self.request = {
            "material_id": "M01",
            "requested_time": "2026-12-01T17:00",
            "hard_requirement": True,
        }

    def test_normal_and_repeat(self):
        first = run(self.request, MockModel(), self.db)
        second = run(self.request, MockModel(), self.db)
        self.assertEqual(first["status"], "completed")
        self.assertEqual(first["draft"]["draft_id"], second["draft"]["draft_id"])
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM drafts").fetchone()[0], 1)

    def test_conflict_requires_confirmation(self):
        request = {**self.request, "requested_time": "2026-12-01T18:00"}
        result = run(request, MockModel(), self.db)
        self.assertEqual(result["status"], "pending_confirmation")
        self.assertEqual(result["draft"]["suggested_time"], "2026-12-01T19:00")
        self.assertEqual(result["draft"]["confirmation_status"], "pending")

    def test_missing_and_risk(self):
        for material, status in (("M02", "incomplete"), ("M03", "blocked")):
            with self.subTest(material=material):
                result = run({**self.request, "material_id": material}, MockModel(), self.db)
                self.assertEqual(result["status"], status)
                self.assertIsNone(result["draft"])

    def test_save_failure_and_step_limit(self):
        result = run(self.request, MockModel(), self.db, fail_tool="save_draft")
        self.assertEqual(result["status"], "incomplete")
        self.assertIsNone(result["draft"])
        self.assertIn("模拟失败", result["reason"])
        limited = run(self.request, MockModel(), self.db, max_steps=1)
        self.assertEqual(limited["status"], "incomplete")
        self.assertIn("上限", limited["reason"])

    def test_cannot_save_unchecked_or_occupied_slot(self):
        tools = Tools(self.request, self.db)
        with self.assertRaises(ToolError):
            tools.call("save_draft", {"proposed_time": "2026-12-01T17:00"})
        tools.call("read_material", {})
        tools.call("check_rules", {})
        tools.call("query_slots", {})
        with self.assertRaises(ToolError):
            tools.call("save_draft", {"proposed_time": "2026-12-01T18:00"})

    def test_opencode_model_sets_session_header(self):
        with patch.dict(os.environ, {"OPENCODE_API_KEY": "test-only"}):
            first = OpenCodeModel()
            second = OpenCodeModel()
        first_session = first.client.default_headers["x-opencode-session"]
        self.assertTrue(first_session)
        self.assertNotEqual(first_session, second.client.default_headers["x-opencode-session"])
        self.assertEqual(
            first.client.default_headers["User-Agent"], "content-review-scheduling-agent/0.1"
        )

    def test_saved_draft_blocks_another_material_at_same_time(self):
        first = run(self.request, MockModel(), self.db)
        second = run({**self.request, "material_id": "M04"}, MockModel(), self.db)
        self.assertEqual(first["draft"]["suggested_time"], "2026-12-01T17:00")
        self.assertEqual(second["status"], "pending_confirmation")
        self.assertNotEqual(second["draft"]["suggested_time"], "2026-12-01T17:00")

    def test_user_can_confirm_pending_draft_from_cli(self):
        result = run({**self.request, "requested_time": "2026-12-01T18:00"}, MockModel(), self.db)
        draft_id = str(result["draft"]["draft_id"])
        completed = subprocess.run(
            [sys.executable, "-m", "src", "confirm", draft_id, "--db", self.db],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(json.loads(completed.stdout)["confirmation_status"], "confirmed")

    def test_cancel_releases_slot_and_resubmission_creates_new_draft(self):
        first = run(self.request, MockModel(), self.db)
        draft_id = first["draft"]["draft_id"]
        completed = subprocess.run(
            [sys.executable, "-m", "src", "cancel", str(draft_id), "--db", self.db],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(json.loads(completed.stdout)["draft_status"], "cancelled")
        again = run(self.request, MockModel(), self.db)
        self.assertEqual(again["status"], "completed")
        self.assertNotEqual(again["draft"]["draft_id"], draft_id)
        self.assertEqual(again["draft"]["suggested_time"], "2026-12-01T17:00")

    def test_batch_continues_after_incomplete_material(self):
        input_file = Path(self.temp.name) / "batch.json"
        input_file.write_text(json.dumps([{**self.request, "material_id": "M02"}, self.request]))
        completed = subprocess.run(
            [sys.executable, "-m", "src", str(input_file), "--db", self.db],
            capture_output=True,
            text=True,
            check=True,
        )
        results = json.loads(completed.stdout)["results"]
        self.assertEqual([item["status"] for item in results], ["incomplete", "completed"])

    def test_agent_reads_required_skill_before_business_tools(self):
        result = run(self.request, MockModel(), self.db)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["trace"][0]["tool"], "Skill")
        self.assertEqual(result["trace"][0]["arguments"]["skill"], "content-scheduling")

    def test_skipping_skill_cannot_execute_business_tools(self):
        class SkipSkillModel(MockModel):
            async def __call__(self, messages, **kwargs):
                if not self.trace:
                    return ChatResponse(
                        content=[ToolCallBlock(id="skip", name="read_material", input="{}")],
                        is_last=True,
                    )
                return await super().__call__(messages, **kwargs)

        result = run(self.request, SkipSkillModel(), self.db)
        self.assertEqual(result["status"], "incomplete")
        self.assertIsNone(result["draft"])
        self.assertIn("Skill", result["reason"])

    def test_output_exposes_saved_and_unsaved_draft_status(self):
        saved = run(self.request, MockModel(), self.db)
        failed = run(self.request, MockModel(), self.db, fail_tool="save_draft")
        self.assertEqual(saved["draft_status"], "saved")
        self.assertEqual(saved["requested_time"], self.request["requested_time"])
        self.assertEqual(failed["draft_status"], "not_saved")
        self.assertIsNone(failed["suggested_time"])

    def test_stale_slot_result_cannot_create_conflicting_draft(self):
        first = Tools(self.request, self.db)
        second = Tools({**self.request, "material_id": "M04"}, self.db)
        for tool in (first, second):
            tool.call("read_material", {})
            tool.call("check_rules", {})
            tool.call("query_slots", {})
        first.call("save_draft", {"proposed_time": "2026-12-01T17:00"})
        with self.assertRaises(ToolError):
            second.call("save_draft", {"proposed_time": "2026-12-01T17:00"})

    def test_model_failure_before_skill_preserves_reason(self):
        class FailingModel(MockModel):
            async def __call__(self, messages, **kwargs):
                raise RuntimeError("模拟网络不可用")

        result = run(self.request, FailingModel(), self.db)
        self.assertEqual(result["status"], "incomplete")
        self.assertIn("模拟网络不可用", result["reason"])

    def test_multiple_skill_calls_respect_tool_limit(self):
        class MultipleSkillModel(MockModel):
            async def __call__(self, messages, **kwargs):
                return ChatResponse(
                    content=[
                        ToolCallBlock(
                            id=f"s{i}",
                            name="Skill",
                            input=json.dumps({"skill": "content-scheduling"}),
                        )
                        for i in range(4)
                    ],
                    is_last=True,
                )

        result = run(self.request, MultipleSkillModel(), self.db, max_steps=2)
        self.assertEqual(result["status"], "incomplete")
        self.assertIn("上限", result["reason"])
        self.assertLessEqual(len(result["trace"]), 2)

    def test_invalid_requested_time_is_not_scheduled(self):
        for value in ("不是时间", "2026-02-30T12:00", "2026-12-01T17:00:00"):
            with self.subTest(value=value):
                result = run({**self.request, "requested_time": value}, MockModel(), self.db)
                self.assertEqual(result["status"], "incomplete")
                self.assertIn("发布时间", result["reason"])
                self.assertEqual(result["trace"], [])

    def test_extra_metadata_does_not_create_duplicate_draft(self):
        request = {**self.request, "requested_time": "2026-12-01T18:00"}
        first = run(request, MockModel(), self.db)
        second = run({**request, "note": "备注"}, MockModel(), self.db)
        self.assertEqual(first["draft"]["draft_id"], second["draft"]["draft_id"])
        self.assertEqual(first["draft"]["suggested_time"], second["draft"]["suggested_time"])

    def test_tool_trace_does_not_reuse_previous_decision_text(self):
        class FirstExplanationModel(MockModel):
            async def __call__(self, messages, **kwargs):
                response = await super().__call__(messages, **kwargs)
                if not self.trace:
                    response.content.insert(0, TextBlock(text="先读取技能说明"))
                return response

        result = run(self.request, FirstExplanationModel(), self.db)
        self.assertEqual(result["status"], "completed")
        read_step = next(step for step in result["trace"] if step["tool"] == "read_material")
        self.assertNotIn("先读取技能说明", read_step["decision_basis"])
        self.assertEqual(read_step["basis_source"], "previous_tool_observation")

    def test_cli_exports_otel_trace_without_polluting_output(self):
        trace_path = Path(self.temp.name) / "trace.jsonl"
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "src",
                "examples/normal.json",
                "--db",
                self.db,
                "--trace-output",
                str(trace_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(json.loads(completed.stdout)["status"], "completed")
        spans = [json.loads(line) for line in trace_path.read_text().splitlines()]
        names = [span["name"] for span in spans]
        self.assertTrue(any("read_material" in name for name in names))
        self.assertTrue(any("save_draft" in name for name in names))

    def test_missing_request(self):
        self.assertEqual(run({}, MockModel(), self.db)["status"], "incomplete")


if __name__ == "__main__":
    unittest.main()
