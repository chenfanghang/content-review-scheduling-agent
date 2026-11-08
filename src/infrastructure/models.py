"""真实模型与离线模型适配。"""

import json
import os
from uuid import uuid4

from agentscope.credential import OpenAICredential
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import TextBlock, ToolCallBlock
from agentscope.model import ChatModelBase, ChatResponse, OpenAIChatModel


class MockModel(ChatModelBase):
    """使用固定策略模拟模型，验证框架流程而非真实模型能力。"""

    def __init__(self):
        super().__init__(
            credential=OpenAICredential(api_key="offline"),
            model="offline-mock",
            parameters=OpenAIChatModel.Parameters(),
            stream=False,
        )
        self.formatter = OpenAIChatFormatter()
        self.trace = []

    async def __call__(self, messages, **kwargs):
        del messages, kwargs
        names = [item["tool"] for item in self.trace]
        observations = [item["result"] for item in self.trace]
        if observations and (
            "error" in observations[-1]
            or observations[-1].get("missing")
            or observations[-1].get("status") == "blocked"
        ):
            return ChatResponse(
                content=[TextBlock(text="工具结果不足或失败，停止。")], is_last=True
            )
        if "Skill" not in names:
            name, args = "Skill", {"skill": "content-scheduling"}
        elif "read_material" not in names:
            name, args = "read_material", {}
        elif "check_rules" not in names:
            name, args = "check_rules", {}
        elif "query_slots" not in names:
            name, args = "query_slots", {}
        elif "save_draft" not in names:
            slots = observations[names.index("query_slots")]
            choices = [slots["requested_time"]] if slots["available"] else slots["alternatives"]
            if not choices:
                return ChatResponse(content=[TextBlock(text="没有可用时段。")], is_last=True)
            name, args = "save_draft", {"proposed_time": choices[0]}
        else:
            return ChatResponse(content=[TextBlock(text="草稿已保存。")], is_last=True)
        return ChatResponse(
            content=[ToolCallBlock(id=f"mock-{len(names)}", name=name, input=json.dumps(args))],
            is_last=True,
        )


def OpenCodeModel():
    """创建使用 OpenCode Go 接口的 AgentScope 模型。"""
    key = os.environ.get("OPENCODE_API_KEY")
    if not key:
        raise ValueError("未设置 OPENCODE_API_KEY")
    return OpenAIChatModel(
        credential=OpenAICredential(
            api_key=key, base_url="https://opencode.ai/inference/go/openai/v1"
        ),
        model="deepseek-v4.1-flash",
        stream=False,
        max_retries=0,
        client_kwargs={
            "timeout": 60,
            "max_retries": 0,
            "default_headers": {
                "x-opencode-session": str(uuid4()),
                "User-Agent": "content-review-scheduling-agent/0.1",
            },
        },
    )
