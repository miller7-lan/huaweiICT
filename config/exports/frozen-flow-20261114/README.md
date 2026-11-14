# 跨文档流程 V3 的 Nexent 接线说明

日期：2026-11-14。**状态：服务和脚本已写好并通过离线测试，MCP 服务用真实 MCP 客户端在测试端口走通过一次；尚未在真实 Nexent 上登记或运行。** 本目录的 `flow-agent.json` 要等本机执行导出后才会出现。

## 组成

| 部分 | 位置 | 说明 |
| --- | --- | --- |
| MCP 服务 | `src/gov_mcp/frozen_qa_flow_server.py`，服务名 `govkb_frozen_flow`，端口 8787 | 独立服务，不影响 8786 上的 `govkb_frozen_qa` |
| 工具 | `govkb_flow_open`、`govkb_flow_submit` | 见下 |
| 草稿 Agent | `govfrozenflow_assistant`，禁用、不发布，`max_steps` 8，无子 Agent、无 Skill | 由 `scripts/frozen_flow_native.py` 登记 |
| 私有运行目录 | `.runtime/frozen-flow-20261114/` | 访问令牌、任务文件、原生运行日志 |

## 调用关系

```
用户问题
  └─ Agent 调 govkb_flow_open(question)
       ├─ 范围门命中 → 返回 STOP，Agent 直接给出停止说明
       └─ 否则 → 返回 task_id、证据、作答规则
            └─ Agent 写草稿，调 govkb_flow_submit(task_id, 草稿)
                 ├─ action=final → Agent 只依据 final 对象组织答复
                 └─ action=revise
                      ├─ reason=FAILED_CHECKS：按失败码改写后再提交
                      └─ reason=MORE_EVIDENCE：结合新增证据重答后再提交
```

服务端执行的是与 `frozen_qa_flow_v3.ask` 相同的决策步骤，同样的草稿序列在两边得到相同的最终结果（有测试）。

## 服务端保证的事

- **预算不由模型掌握**：每个任务最多接收 3 份草稿，计数在处理之前写盘，重启或重试都不会重置。
- **已结束的任务只回放**：再次提交返回已保存的结果，不重新处理。
- **格式不对的草稿被拒绝且不消耗预算**；任务号不存在、语料或提示词方案与任务不符时拒绝。
- **范围门在检索之前**，命中时不创建任务。
- **展示内容再核验**：返回的 `final_checks` 是对最终展示内容的重新核验。
- 源码在服务启动后被改动时拒绝请求，需重启。

## 与 38、39 号旧草稿的关系

旧草稿的提示词要求检索、核验各一次；本草稿允许在服务端指示下最多改写两次。旧草稿、旧服务和旧导出不改动，登记脚本会在前后比对其他 Agent 的指纹。

## 本机操作

见 [本机待测清单](../../../docs/implementation/crossdoc-stop-20261114/本机待测清单-2026-11-14.md)第 4—7 项。
