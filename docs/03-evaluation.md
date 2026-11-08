# 评估

以下为本地 Mock 执行结果，文件包含输入、输出和工具调用记录；真实模型结果见下一节。

| 验收场景 | 输入 | 实际输出 |
| --- | --- | --- |
| 正常 | [normal.json](../examples/normal.json) | [normal.output.json](../examples/normal.output.json) |
| 信息缺失 | [missing.json](../examples/missing.json) | [missing.output.json](../examples/missing.output.json) |
| 规则风险 | [risk.json](../examples/risk.json) | [risk.output.json](../examples/risk.output.json) |
| 时段冲突 | [conflict.json](../examples/conflict.json) | [conflict.output.json](../examples/conflict.output.json) |
| 重复提交 | [normal.json](../examples/normal.json) | [repeat.output.json](../examples/repeat.output.json)，数据库记录数另由自动测试核对 |
| 保存失败 | normal.json，注入 `--fail-tool save_draft` | [save-failure.output.json](../examples/save-failure.output.json) |

[测试结果](../examples/test-results.txt)：24 项通过；使用取消替代时段待确认标记的变异验证，相关测试按预期失败。真实模型已运行正常、冲突、信息缺失、规则风险和保存失败场景；稳定性和真实业务资料有待进一步验证。

## 真实模型验证

使用 Artefact 项目中配置的 OpenCode Go 账户及 `deepseek-v4.1-flash` 运行：

- [正常路径](../examples/normal.opencode.output.json)：读取 Skill、素材，检查规则、查询时段、保存草稿，状态为 completed。
- [冲突路径](../examples/conflict.opencode.output.json)：读取 Skill、素材，检查规则、查询时段，模型发现硬性要求时段不可用后选择备选时段并保存待确认草稿，状态为 pending_confirmation；尚未发布。

补充验证：[信息缺失](../examples/missing.opencode.output.json)停止并说明缺口；[审核风险](../examples/risk.opencode.output.json)阻断且未保存；[保存失败](../examples/save-failure.opencode.output.json)返回未完成并说明失败。上述场景各运行一次；稳定性有待通过重复运行评估。

## DeepEval 固定场景评测

使用 DeepEval 4.3.3 的 `BaseMetric` 自定义确定性业务指标，不使用 LLM 裁判。六个固定场景分别使用独立临时 SQLite 数据库；重复提交场景在同一数据库中运行两次。指标检查 Skill 读取、任务状态及实际有效草稿，冲突允许保存待确认草稿或停止并请求确认。模型/API 异常标记为无效运行，不计作 Agent 失败；其他运行失败按实际证据记录。

2026-11-08 使用 OpenCode Go `deepseek-v4.1-flash` 各运行一次：正常、冲突、信息缺失、审核风险、保存失败及重复提交均通过，详见 [完整结果与 Trace](../examples/deepeval-opencode.json)。首次运行后发现客户端关闭警告，修复后单独复跑重复提交并通过；其余五个结果来自资源关闭修复之前的同一批运行。指标通过正确待确认草稿及错误确认状态的对照测试。本轮每个场景运行一次；后续可通过重复运行评估稳定性，并补充 token、费用和输出措辞评估。OpenCode Go 的使用范围须按服务方要求确认。

```bash
uv sync --dev
uv run python -m evals.run_eval --model mock --output /tmp/scheduling-eval.json
set -a; source .env; set +a
uv run python -m evals.run_eval --model opencode --output /tmp/scheduling-eval-real.json
```

冲突评分器曾把未查询时段的任意错误误判为通过，现已要求 Trace 含该时段不可用的查询结果；未保存时还须说明冲突并请求确认。使用修正后的指标重新评分原始运行记录，六个场景均通过，见 [重新评分结果](../examples/deepeval-rescored.json)；这是对原始记录的重新评分，不是新一轮模型运行。关键词检查可能漏判合理表述，需人工审阅。

## Trace 依据说明

正常和冲突路径已重新真实运行，记录标记 `basis_source`：`model` 表示该轮模型提供的简短说明；`previous_tool_observation` 是程序记录的上一工具客观返回。此次模型未输出逐步文字说明，记录展示了工具观察和后续动作。此前 DeepEval 记录保留原始结果，未重新生成。

## OpenTelemetry Trace

使用 AgentScope 2.0.11 的 TracingMiddleware 和 OpenTelemetry SDK，本地导出 [真实模型运行的 11 条 span](../examples/normal.otel.jsonl)，包含 5 次模型调用、5 次工具调用及 Agent 调用；采集的是合成数据。CLI 可通过 `--trace-output 路径` 输出 JSONL，默认不启用；span 包含模型和工具输入输出。它记录实际模型和工具调用。

分层重构后重新运行 24 项测试，并使用真实模型分别验证正常和时段冲突路径：正常返回 completed，冲突返回 pending_confirmation，两条路径均生成 OpenTelemetry span。本次验证使用临时数据库，原有真实模型记录作为历史证据保留。
