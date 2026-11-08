# 素材审核与发布排期 Agent

小影校招项目 1：读取素材、检查审核风险和排期冲突，生成可确认的发布排期草稿。Agent 根据工具结果选择下一步；项目使用合成数据，仅保存草稿。

**技术栈**：Python 3.11+、AgentScope 2.0.11、SQLite、OpenTelemetry、DeepEval。使用 uv 管理依赖，依赖版本见 `uv.lock`。

## 快速运行

```bash
uv sync --dev
uv run python -m src examples/conflict.json --model mock --db /tmp/scheduling-demo.db
uv run python -m unittest discover -s tests -v
```

示例中 M01 的 18:00 已占用；Mock 会选择 19:00 并保存待确认草稿。重复执行相同命令会复用有效草稿。`mock` 使用固定策略，仅验证流程；真实模型运行记录见下方评审入口。

### 真实模型

在本地创建 `.env`，参考 `.env.example` 设置 `OPENCODE_API_KEY`，然后运行：

```bash
set -a; source .env; set +a
uv run python -m src examples/conflict.json --model opencode --db /tmp/scheduling-real.db
```

默认模型为 OpenCode Go 的 `deepseek-v4.1-flash`；调用消耗账户额度。OpenCode Go 面向编程 Agent 流量，本项目的使用范围需按服务方要求确认。

## 输入与操作

单条输入格式如下；批量输入使用由多个此类对象组成的 JSON 数组，按顺序逐项处理。

```json
{"material_id":"M01","requested_time":"2026-12-01T18:00","hard_requirement":true}
```

发布时间格式为 `YYYY-MM-DDTHH:MM`；输入和排期数据使用相同业务时区，原型不做时区转换或过去时间校验。运行 `uv run python -m src examples/batch.json --db /tmp/scheduling-batch.db` 可查看批量示例。

使用输出中的草稿 ID 进行人工确认或取消：

```bash
uv run python -m src confirm 1 --db /tmp/scheduling-demo.db
uv run python -m src cancel 1 --db /tmp/scheduling-demo.db
```

输出包含 `status`、审核状态和风险、要求与建议时段、确认与草稿保存状态、原因及 `trace`。`completed` 表示草稿已保存，`pending_confirmation` 表示替代时段待确认，`blocked` 表示审核阻断，`incomplete` 表示未完成。草稿状态不代表内容已发布。

`--max-steps` 设置模型轮次和工具调用上限；`--fail-tool save_draft` 注入保存失败。`--trace-output /tmp/agent-trace.jsonl` 可导出 OpenTelemetry spans。Trace 包含模型和工具输入输出，示例使用合成数据。

## 评审入口

- [需求与验收](docs/01-requirements.md) · [方案与分层](docs/02-solution.md) · [评估结果](docs/03-evaluation.md)
- [AI 使用与个人贡献](docs/04-ai-usage.md) · [项目复盘](docs/05-retrospective.md)
- [真实模型运行与评测记录](examples/) · [业务 Skill](skills/content-scheduling/SKILL.md)

```text
src/domain/          业务请求身份与错误
src/application/     Agent 循环与审核排期工具
src/infrastructure/  SQLite、本地数据、模型接入
src/presentation/    CLI
src/composition.py   依赖装配
```

固定场景评测：`uv run python -m evals.run_eval --model mock --output /tmp/scheduling-eval.json`。真实模型评测需设置密钥并将 `mock` 改为 `opencode`。结果和局限见[评估文档](docs/03-evaluation.md)。
