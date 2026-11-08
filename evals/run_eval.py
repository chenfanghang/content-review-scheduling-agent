"""使用 DeepEval 运行排期 Agent 的固定业务场景。"""

import argparse
import json
import os
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path

from deepeval.metrics import BaseMetric
from deepeval.test_case import LLMTestCase, ToolCall

from src.composition import run
from src.infrastructure.models import MockModel, OpenCodeModel

BASE = {"material_id": "M01", "requested_time": "2026-12-01T17:00", "hard_requirement": True}
CASES = {
    "normal": (BASE, None),
    "conflict": ({**BASE, "requested_time": "2026-12-01T18:00"}, None),
    "missing": ({**BASE, "material_id": "M02"}, None),
    "risk": ({**BASE, "material_id": "M03"}, None),
    "save_failure": (BASE, "save_draft"),
    "repeat": (BASE, None),
}


def snapshot(db_path):
    """读取实际有效草稿作为独立判定依据。"""
    with closing(sqlite3.connect(db_path)) as db:
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='drafts'"
        ).fetchone()
        if not exists:
            return []
        return [
            {"id": row[0], **json.loads(row[1])}
            for row in db.execute("SELECT id, payload FROM drafts WHERE active = 1 ORDER BY id")
        ]


class SchedulingMetric(BaseMetric):
    """使用确定性业务约束评测 Agent 的结果和实际草稿。"""

    threshold = 1.0

    def measure(self, test_case, *args, **kwargs):
        metadata = test_case.metadata
        case = metadata["case"]
        results = metadata["results"]
        drafts = metadata["drafts"]
        result = results[-1]
        trace = result.get("trace", [])
        failures = []
        if (
            not trace
            or trace[0].get("tool") != "Skill"
            or trace[0].get("result", {}).get("status") != "read"
        ):
            failures.append("未先读取业务 Skill")
        if case == "normal":
            if (
                result.get("status") != "completed"
                or len(drafts) != 1
                or drafts[0].get("suggested_time") != BASE["requested_time"]
            ):
                failures.append("正常排期或草稿状态错误")
        elif case == "conflict":
            conflict_observed = any(
                item.get("tool") == "query_slots"
                and item.get("result", {}).get("requested_time") == "2026-12-01T18:00"
                and item.get("result", {}).get("available") is False
                for item in trace
            )
            valid_pending = (
                len(drafts) == 1
                and drafts[0].get("suggested_time") in {"2026-12-01T19:00", "2026-12-01T20:00"}
                and drafts[0].get("confirmation_status") == "pending"
                and result.get("status") == "pending_confirmation"
            )
            valid_stop = (
                not drafts
                and result.get("status") == "incomplete"
                and "确认" in (result.get("reason") or "")
                and any(
                    word in (result.get("reason") or "")
                    for word in ("冲突", "占用", "不可用", "18:00")
                )
            )
            if not conflict_observed or not (valid_pending or valid_stop):
                failures.append("冲突未正确标记待确认或停止")
        elif case == "missing":
            if (
                result.get("status") != "incomplete"
                or drafts
                or not any(t.get("result", {}).get("missing") for t in trace)
            ):
                failures.append("素材缺失未正确停止")
        elif case == "risk":
            if result.get("status") != "blocked" or drafts or not result.get("risk_reasons"):
                failures.append("审核风险未正确阻断")
        elif case == "save_failure":
            if (
                result.get("status") != "incomplete"
                or drafts
                or not any(
                    t.get("tool") == "save_draft" and t.get("result", {}).get("error")
                    for t in trace
                )
            ):
                failures.append("保存失败未正确报告")
        elif case == "repeat" and (
            len(drafts) != 1
            or len(results) != 2
            or any(
                not r.get("draft") or r["draft"].get("draft_id") != drafts[0]["id"] for r in results
            )
        ):
            failures.append("重复请求未复用同一草稿")
        self.score = 0.0 if failures else 1.0
        self.success = not failures
        self.reason = "; ".join(failures) if failures else "业务断言通过"
        return self.score

    async def a_measure(self, test_case, *args, **kwargs):
        return self.measure(test_case, *args, **kwargs)

    @property
    def __name__(self):
        return "排期业务正确性"


def evaluate_case(case, model_name):
    """执行一次场景，隔离数据库并记录框架评分。"""
    request, fail_tool = CASES[case]
    results = []
    start = time.monotonic()
    with tempfile.TemporaryDirectory() as directory:
        db = str(Path(directory) / "drafts.db")
        for _ in range(2 if case == "repeat" else 1):
            model = OpenCodeModel() if model_name == "opencode" else MockModel()
            results.append(run(request, model, db, fail_tool=fail_tool))
            if str(results[-1].get("reason", "")).startswith("模型或 Agent 运行失败"):
                break
        drafts = snapshot(db)
    invalid = any(
        str(result.get("reason", "")).startswith("模型或 Agent 运行失败") for result in results
    )
    test_case = LLMTestCase(
        input=json.dumps(request, ensure_ascii=False),
        actual_output=json.dumps(results, ensure_ascii=False),
        tools_called=[
            ToolCall(name=item["tool"], input_parameters=item.get("arguments"))
            for result in results
            for item in result.get("trace", [])
        ],
        metadata={"case": case, "results": results, "drafts": drafts},
    )
    metric = SchedulingMetric()
    score = None if invalid else metric.measure(test_case)
    return {
        "case": case,
        "model": model_name,
        "classification": "invalid" if invalid else ("passed" if score == 1 else "failed"),
        "score": score,
        "reason": results[-1].get("reason") if invalid else metric.reason,
        "elapsed_seconds": round(time.monotonic() - start, 2),
        "results": results,
        "drafts": drafts,
    }


def main():
    parser = argparse.ArgumentParser(description="DeepEval 排期业务评测")
    parser.add_argument("--model", choices=("mock", "opencode"), default="mock")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.model == "opencode" and not os.environ.get("OPENCODE_API_KEY"):
        parser.error("真实模型评测需要 OPENCODE_API_KEY")
    reports = []
    for case in CASES:
        report = evaluate_case(case, args.model)
        reports.append(report)
        print(f"{case}: {report['classification']}", flush=True)
    Path(args.output).write_text(
        json.dumps(
            {"framework": "DeepEval", "metric": "排期业务正确性", "runs": reports},
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
