"""装配命令行、Agent 与本地数据依赖。"""

from src.application.agent import run as run_agent
from src.application.tools import Tools as SchedulingTools
from src.infrastructure.catalog import LocalCatalog
from src.infrastructure.drafts import DraftRepository


def Tools(request, db_path, fail_tool=None):
    """装配一次任务的业务工具。"""
    return SchedulingTools(request, DraftRepository(db_path), LocalCatalog(), fail_tool)


def run(request, model, db_path, max_steps=10, fail_tool=None):
    """装配一次任务并运行 Agent。"""
    return run_agent(request, model, Tools(request, db_path, fail_tool), max_steps)
