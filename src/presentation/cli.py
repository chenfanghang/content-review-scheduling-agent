"""命令行入口。"""

import argparse
import json
import sys
from pathlib import Path

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

from src.composition import run
from src.domain.scheduling import ToolError
from src.infrastructure.drafts import cancel_draft, confirm_draft
from src.infrastructure.models import MockModel, OpenCodeModel


def main():
    parser = argparse.ArgumentParser(description="素材审核与发布排期 Agent")
    parser.add_argument("input", help="JSON 输入文件，或 confirm 操作")
    parser.add_argument("draft_id", nargs="?", type=int, help="待确认的草稿 ID")
    parser.add_argument("--model", choices=("mock", "opencode"), default="mock")
    parser.add_argument("--db", default="drafts.db")
    parser.add_argument("--trace-output", help="将 OpenTelemetry spans 写入 JSONL 文件")
    parser.add_argument("--max-steps", type=int, default=10)
    parser.add_argument(
        "--fail-tool", choices=("read_material", "check_rules", "query_slots", "save_draft")
    )
    args = parser.parse_args()
    try:
        if args.input in ("confirm", "cancel"):
            if args.draft_id is None:
                raise ValueError("确认操作需要草稿 ID")
            result = (confirm_draft if args.input == "confirm" else cancel_draft)(
                args.db, args.draft_id
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        request = json.loads(Path(args.input).read_text())
        trace_file = None
        provider = None
        if args.trace_output:
            trace_file = Path(args.trace_output).open("w", encoding="utf-8")  # noqa: SIM115
            provider = TracerProvider(shutdown_on_exit=False)
            provider.add_span_processor(
                SimpleSpanProcessor(
                    ConsoleSpanExporter(
                        out=trace_file, formatter=lambda span: span.to_json(indent=None) + "\n"
                    )
                )
            )
            trace.set_tracer_provider(provider)

        def process(item):
            model = OpenCodeModel() if args.model == "opencode" else MockModel()
            result = run(item, model, args.db, args.max_steps, args.fail_tool)
            if isinstance(item, dict):
                result["material_id"] = item.get("material_id")
            return result

        try:
            result = (
                {"results": [process(item) for item in request]}
                if isinstance(request, list)
                else process(request)
            )
        finally:
            if provider:
                provider.shutdown()
                trace_file.close()
    except (OSError, ValueError, ToolError) as exc:
        print(json.dumps({"status": "incomplete", "reason": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
