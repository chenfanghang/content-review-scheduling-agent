"""验证 DeepEval 业务指标能识别关键错误。"""

import unittest

from deepeval.test_case import LLMTestCase

from evals.run_eval import SchedulingMetric


class EvaluationMetricTest(unittest.TestCase):
    def test_conflict_accepts_pending_and_rejects_unconfirmed_claim(self):
        result = {
            "status": "pending_confirmation",
            "trace": [
                {"tool": "Skill", "result": {"status": "read"}},
                {
                    "tool": "query_slots",
                    "result": {"requested_time": "2026-12-01T18:00", "available": False},
                },
            ],
        }
        draft = {"id": 1, "suggested_time": "2026-12-01T19:00", "confirmation_status": "pending"}
        case = LLMTestCase(
            input="冲突",
            actual_output="草稿",
            metadata={"case": "conflict", "results": [result], "drafts": [draft]},
        )
        self.assertEqual(SchedulingMetric().measure(case), 1)
        draft["confirmation_status"] = "confirmed"
        self.assertEqual(SchedulingMetric().measure(case), 0)

    def test_conflict_rejects_unrelated_error_without_query(self):
        result = {
            "status": "incomplete",
            "reason": "发生任意错误",
            "trace": [{"tool": "Skill", "result": {"status": "read"}}],
        }
        case = LLMTestCase(
            input="冲突",
            actual_output="错误",
            metadata={"case": "conflict", "results": [result], "drafts": []},
        )
        self.assertEqual(SchedulingMetric().measure(case), 0)

    def test_conflict_accepts_explicit_confirmation_after_query(self):
        result = {
            "status": "incomplete",
            "reason": "18:00 已被占用，请确认 19:00",
            "trace": [
                {"tool": "Skill", "result": {"status": "read"}},
                {
                    "tool": "query_slots",
                    "result": {"requested_time": "2026-12-01T18:00", "available": False},
                },
            ],
        }
        case = LLMTestCase(
            input="冲突",
            actual_output=result["reason"],
            metadata={"case": "conflict", "results": [result], "drafts": []},
        )
        self.assertEqual(SchedulingMetric().measure(case), 1)


if __name__ == "__main__":
    unittest.main()
