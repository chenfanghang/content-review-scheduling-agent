"""排期领域错误与业务请求身份。"""

import json


class ToolError(Exception):
    """业务工具调用失败。"""


def request_key(request):
    """以素材、要求时段和硬性标记识别同一业务请求。"""
    return json.dumps(
        {name: request[name] for name in ("material_id", "requested_time", "hard_requirement")},
        sort_keys=True,
        ensure_ascii=False,
    )
