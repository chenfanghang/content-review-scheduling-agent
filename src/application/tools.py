"""素材审核和排期工具。"""

from src.domain.scheduling import ToolError, request_key


class Tools:
    """组织一次任务的素材、审核、时段和草稿操作。"""

    def __init__(self, request, repository, catalog, fail_tool=None):
        self.request = request
        self.repository = repository
        self.catalog = catalog
        self.fail_tool = fail_tool
        self.material = None
        self.review = None
        self.checked = {}
        self.saved = None

    def call(self, name, args):
        if name == self.fail_tool:
            raise ToolError(f"{name} 模拟失败")
        methods = {
            "read_material": self.read_material,
            "check_rules": self.check_rules,
            "query_slots": self.query_slots,
            "save_draft": self.save_draft,
        }
        if name not in methods:
            raise ToolError("未知工具")
        try:
            return methods[name](**args)
        except (TypeError, ValueError, OSError) as exc:
            raise ToolError(str(exc)) from exc

    def read_material(self):
        self.material = self.catalog.materials().get(self.request["material_id"])
        if self.material is None:
            return {"found": False, "missing": ["素材不存在"]}
        missing = [key for key in ("title", "description") if not self.material.get(key)]
        return {"found": True, "material": self.material, "missing": missing}

    def check_rules(self):
        if not self.material or not all(self.material.get(k) for k in ("title", "description")):
            raise ToolError("必须先读取完整素材")
        blocked = self.material["risk"] == "block"
        self.review = {
            "status": "blocked" if blocked else "passed",
            "reasons": ["包含未经证实的绝对化宣传"] if blocked else [],
        }
        return self.review

    def query_slots(self):
        schedule = self.catalog.schedule()
        requested = self.request["requested_time"]
        occupied = list(schedule["occupied"])
        occupied.extend(self.repository.occupied_times(request_key(self.request)))
        self.checked = {
            "requested_time": requested,
            "available": requested not in occupied,
            "alternatives": [x for x in schedule["candidates"] if x not in occupied],
        }
        return self.checked

    def save_draft(self, proposed_time):
        if not self.review or self.review["status"] != "passed" or not self.checked:
            raise ToolError("保存前必须通过审核并查询时段")
        allowed = [self.request["requested_time"]] if self.checked["available"] else []
        allowed += self.checked["alternatives"]
        if proposed_time not in allowed:
            raise ToolError("建议时段未经查询或已占用")
        payload = {
            "material_id": self.request["material_id"],
            "requested_time": self.request["requested_time"],
            "hard_requirement": self.request["hard_requirement"],
            "suggested_time": proposed_time,
            "confirmation_status": "pending"
            if proposed_time != self.request["requested_time"]
            else "not_required",
        }
        self.saved = self.repository.save(
            request_key(self.request), payload, self.catalog.schedule()["occupied"]
        )
        return self.saved
