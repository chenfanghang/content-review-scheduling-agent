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

## 技术选型与取舍

**Python 与 CLI。** 项目使用 Python 实现，CLI 负责接收素材请求和输出结果。评审可以通过命令直接运行案例、查看 Trace 和执行测试，适合本次单机交付。

**AgentScope。** 项目先用原生工具循环打通链路，再采用 AgentScope 组织 Agent、工具和 Skill。AgentScope 提供的工具调用与 Skill 加载机制适合本题的单 Agent 流程。方案讨论中也考虑过 LangGraph 和 Google ADK；LangGraph 适合需要显式编排复杂状态和中断恢复的流程，ADK 同样提供 Agent 和工具能力。本次选型优先考虑当前业务范围与可解释性。

**DeepSeek 与离线 Mock。** 真实模型使用 OpenCode Go 的 `deepseek-v4.1-flash`，通过兼容接口完成模型决策和工具调用；离线 Mock 用于本地复现流程。模型选择基于现有账户可用性和工具调用需求。

**SQLite。** 草稿需要跨 CLI 调用保存，并支持去重和时段冲突检查。SQLite 无需额外启动数据库服务，写事务也适合本项目的本地草稿操作。

**单 Agent。** 素材读取、审核、时段查询和草稿保存由一个 Agent 调用四类工具完成，运行记录集中在同一条调用链中，便于核对模型如何根据工具结果调整行动。

**DeepEval 与 OpenTelemetry。** DeepEval 使用确定性业务指标评估固定场景，测试实际状态和草稿结果；方案讨论中也考虑过支持 Agent 指标的 Ragas。业务 JSON Trace 用于核对工具参数、返回和后续动作，AgentScope 的 OpenTelemetry 中间件补充模型和工具调用链。

## 代码与技术取舍

`domain/` 定义业务请求身份和错误；`application/` 组织 Agent 与业务工具；`infrastructure/` 提供 SQLite、本地数据和模型接入；`presentation/` 提供 CLI；`composition.py` 装配依赖。SQLite 以写事务处理草稿去重与冲突核对，保留旧版草稿表记录。

业务 JSON Trace 记录动作、参数、工具结果和可获得的简要说明。`basis_source=model` 是模型主动输出的说明；`previous_tool_observation` 是程序记录的上一工具结果。AgentScope `TracingMiddleware` 可将模型、工具及 Agent spans 导出为 OpenTelemetry JSONL。

离线 Mock 是固定策略，适合检查流程。合成数据的候选时段固定，不自动跨日期搜索；项目未接入真实素材库、审核系统或发布日历。
