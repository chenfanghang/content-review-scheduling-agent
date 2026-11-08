# 方案与实现

## 调用流程

```text
CLI 输入 → 逐项处理 → Agent 读取业务 Skill → 模型选择工具
                                         ↓
                       素材读取 / 审核 / 时段查询 / 草稿保存
                                         ↓
                         工具结果返回模型 → 继续或结束
```

AgentScope 2.0.11 的 `Agent`、`Toolkit`、`FunctionTool` 和 `OpenAIChatModel` 负责模型决策与工具调度；OpenCode Go 提供 `deepseek-v4.1-flash`。每项素材先读取 [排期 Skill](../skills/content-scheduling/SKILL.md)，读取失败或跳过时拒绝业务工具。模型可在工具返回后继续调用或结束；Skill 调用计入上限。

## 业务边界

- 保存前必须读取完整素材、审核通过并查询时段；保存事务重新核对占用情况。
- 素材共享一份排期，以完整日期与分钟判断冲突；待确认和已确认的有效草稿均占位。
- 替代时段标记为待确认，保留原要求；用户可通过 CLI 确认或取消，取消释放时段。
- 以素材 ID、要求时段和硬性标记识别业务请求；重复请求返回已有有效草稿，取消后重提创建新草稿。
- 保存结果由 SQLite 操作决定；不提供真实发布工具。

## 代码与技术取舍

`domain/` 定义业务请求身份和错误；`application/` 组织 Agent 与业务工具；`infrastructure/` 提供 SQLite、本地数据和模型接入；`presentation/` 提供 CLI；`composition.py` 装配依赖。SQLite 以写事务处理草稿去重与冲突核对，保留旧版草稿表记录。

业务 JSON Trace 记录动作、参数、工具结果和可获得的简要说明。`basis_source=model` 是模型主动输出的说明；`previous_tool_observation` 是程序记录的上一工具结果。AgentScope `TracingMiddleware` 可将模型、工具及 Agent spans 导出为 OpenTelemetry JSONL。

离线 Mock 是固定策略，适合检查流程。合成数据的候选时段固定，不自动跨日期搜索；项目未接入真实素材库、审核系统或发布日历。
