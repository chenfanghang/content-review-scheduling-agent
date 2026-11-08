"""AgentScope 排期决策与运行记录。"""

import asyncio
import json
from datetime import datetime
from pathlib import Path

from agentscope.agent import Agent, ReActConfig
from agentscope.event import (
    ModelCallStartEvent,
    TextBlockEndEvent,
    ToolCallDeltaEvent,
    ToolCallStartEvent,
    ToolResultEndEvent,
)
from agentscope.message import Msg, TextBlock, ToolResultState, UserMsg
from agentscope.middleware import TracingMiddleware
from agentscope.model import OpenAIChatModel
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import FunctionTool, Toolkit

from src.domain.scheduling import ToolError

ROOT = Path(__file__).resolve().parents[2]


def run(request, model, tools, max_steps=10):
    """通过 AgentScope 运行 ReAct 循环并返回可核对的工具记录。"""
    required = ("material_id", "requested_time", "hard_requirement")
    if (
        not isinstance(request, dict)
        or not all(k in request for k in required)
        or not isinstance(request.get("hard_requirement"), bool)
    ):
        return {
            "status": "incomplete",
            "reason": "缺少素材 ID、发布时间或硬性要求标记",
            "trace": [],
        }
    if not request["material_id"] or not request["requested_time"]:
        return {"status": "incomplete", "reason": "素材 ID 和发布时间不能为空", "trace": []}
    try:
        requested_time = request["requested_time"]
        if (
            not isinstance(requested_time, str)
            or datetime.strptime(requested_time, "%Y-%m-%dT%H:%M").strftime("%Y-%m-%dT%H:%M")
            != requested_time
        ):
            raise ValueError
    except ValueError:
        return {
            "status": "incomplete",
            "reason": "发布时间须为有效的 YYYY-MM-DDTHH:MM",
            "trace": [],
        }
    if max_steps < 1:
        return {"status": "incomplete", "reason": "步数上限必须大于零", "trace": []}
    trace = []
    if hasattr(model, "trace"):
        model.trace = trace
    error = None
    tool_limit = False
    skill_ready = False
    skill_violation = False
    agent = None
    decision_text = []

    def execute(name, args):
        nonlocal error, tool_limit, skill_violation
        if not skill_ready or skill_violation:
            skill_violation = True
            error = "必须先读取排期审核 Skill"
            if len(trace) < max_steps:
                trace.append(
                    {
                        "step": len(trace) + 1,
                        "tool": name,
                        "arguments": args,
                        "result": {"error": error},
                        "decision_basis": None,
                    }
                )
            return {"error": error}
        if len(trace) >= max_steps:
            tool_limit = True
            result = {"error": "已达到工具调用次数上限"}
        else:
            try:
                result = tools.call(name, args)
                error = None
            except ToolError as exc:
                error = str(exc)
                result = {"error": error}
            trace.append(
                {
                    "step": len(trace) + 1,
                    "tool": name,
                    "arguments": args,
                    "result": result,
                    "decision_basis": " ".join(decision_text)
                    or (
                        "上一工具观察："
                        + trace[-1]["tool"]
                        + " "
                        + json.dumps(trace[-1]["result"], ensure_ascii=False)
                        if trace
                        else None
                    ),
                    "basis_source": "model" if decision_text else "previous_tool_observation",
                }
            )
        return result

    def read_material():
        """读取素材并检查缺失字段。"""
        return execute("read_material", {})

    def check_rules():
        """检查素材审核规则。"""
        return execute("check_rules", {})

    def query_slots():
        """查询要求时段和备选时段。"""
        return execute("query_slots", {})

    def save_draft(proposed_time: str):
        """保存草稿。改变原时段会标记待确认。

        Args:
            proposed_time: 建议的发布时间，必须是查询结果中的可用时段。
        """
        return execute("save_draft", {"proposed_time": proposed_time})

    permission = PermissionDecision(
        behavior=PermissionBehavior.ALLOW, message="允许执行本地排期工具"
    )
    toolkit = Toolkit(
        skills_or_loaders=[str(ROOT / "skills/content-scheduling")],
        tools=[
            FunctionTool(function, permission=permission, is_concurrency_safe=False)
            for function in (read_material, check_rules, query_slots, save_draft)
        ],
    )
    agent = Agent(
        name="排期助手",
        system_prompt="必须先用 Skill 工具读取 content-scheduling，之后才能调用业务工具。使用工具核实素材、规则和时段。工具失败或信息不足时停止并说明。只有通过审核且时段可用才保存草稿。更改硬性时间须待确认，不能声称发布。每一步简要说明依据。",
        model=model,
        toolkit=toolkit,
        middlewares=[TracingMiddleware()],
        react_config=ReActConfig(max_iters=max_steps),
    )
    reason = None
    try:

        async def execute_agent():
            nonlocal skill_ready, tool_limit
            skill_calls = {}
            answer = None
            async for event in agent.reply_stream(
                UserMsg("user", json.dumps(request, ensure_ascii=False)), yield_final_msg=True
            ):
                if isinstance(event, ModelCallStartEvent):
                    decision_text.clear()
                elif isinstance(event, TextBlockEndEvent) and event.text:
                    decision_text.append(event.text)
                elif isinstance(event, ToolCallStartEvent) and event.tool_call_name == "Skill":
                    if len(trace) + len(skill_calls) >= max_steps:
                        tool_limit = True
                        break
                    skill_calls[event.tool_call_id] = ""
                elif isinstance(event, ToolCallDeltaEvent) and event.tool_call_id in skill_calls:
                    skill_calls[event.tool_call_id] += event.delta
                elif isinstance(event, ToolResultEndEvent) and event.tool_call_id in skill_calls:
                    try:
                        args = json.loads(skill_calls.pop(event.tool_call_id))
                    except ValueError:
                        args = {}
                    skill_ready = (
                        event.state == ToolResultState.SUCCESS
                        and args.get("skill") == "content-scheduling"
                    )
                    trace.append(
                        {
                            "step": len(trace) + 1,
                            "tool": "Skill",
                            "arguments": args,
                            "result": {"status": "read" if skill_ready else "failed"},
                            "decision_basis": None,
                        }
                    )
                elif isinstance(event, Msg):
                    answer = event
            return answer

        async def execute_and_close():
            try:
                return await execute_agent()
            finally:
                if isinstance(model, OpenAIChatModel):
                    await model.client.close()

        answer = asyncio.run(execute_and_close())
        if answer is None:
            raise ToolError("Agent 未完成运行")
        reason = (
            " ".join(item.text for item in answer.content if isinstance(item, TextBlock)) or None
        )
    except Exception as exc:  # noqa: BLE001 - 将外部模型调用失败转换为未完成状态。
        reason = f"模型或 Agent 运行失败：{type(exc).__name__}: {exc}"
        error = reason
    if tool_limit:
        error = "已达到模型决策或工具调用次数上限"
    elif skill_violation or (not skill_ready and not error):
        error = "未先读取排期审核 Skill，任务未完成"
    elif agent.state.cur_iter >= max_steps:
        error = "已达到模型决策或工具调用次数上限"
    if error:
        status, reason = "incomplete", error
    elif tools.saved:
        status = (
            "pending_confirmation"
            if tools.saved["confirmation_status"] == "pending"
            else "completed"
        )
        reason = None
    elif tools.review and tools.review["status"] == "blocked":
        status = "blocked"
    else:
        status = "incomplete"
    return {
        "status": status,
        "material_id": request["material_id"],
        "review_status": tools.review["status"] if tools.review else "not_reviewed",
        "risk_reasons": tools.review["reasons"] if tools.review else [],
        "requested_time": request["requested_time"],
        "suggested_time": tools.saved["suggested_time"] if tools.saved else None,
        "confirmation_status": tools.saved["confirmation_status"] if tools.saved else None,
        "draft_status": "saved" if tools.saved else "not_saved",
        "review": tools.review,
        "draft": tools.saved,
        "reason": reason,
        "trace": trace,
    }
