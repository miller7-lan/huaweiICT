# 河南后继资料更新智能体组导出说明

`agent.json` 由 `scripts/export_successor_update_native.py` 于2026年11月5日从本机 Nexent 原生导出接口只读取得，未调用模型、未发布。导出前逐项比对：主控、整理、核验三个草稿与2026年10月21日A6运行账本一致，主控 Skill `govkb-successor-update` 与 `config/skills` 中的源文件一致。核对回执见 `evidence/agent-autonomy-repair-20261020-a6/actual-export-20261105.json`。

| 角色 | 名称 | 步数上限 | MCP工具（服务 `govkb_successor_update`） |
| --- | --- | --- | --- |
| 主控 | govsuccessor_controller | 6 | govkb_successor_state、govkb_successor_finish；读取 Skill，委派两个子Agent |
| 整理 | govsuccessor_extractor | 4 | govkb_successor_get、govkb_successor_submit |
| 核验 | govsuccessor_reviewer | 4 | govkb_successor_get、govkb_successor_review |

三者均为禁用、未发布草稿，模型为 `deepseek-flash`（温度0、关闭思考）。原始ZIP（含 Skill）在本机 `artifacts/competition-20261105/`，作品附件中位于“Nexent智能体配置”目录。

## 接收机器接线

1. MCP服务源码为 `src/gov_mcp/successor_update_server.py` 与 `successor_update.py`，历史端口8784，仅监听本机；令牌存接收方私有目录，不从旧机复制。
2. 先在 Nexent 注册同名 MCP 服务及5个工具，再导入本 JSON；JSON内的模型、工具和智能体编号是本机编号，导入后须逐项重绑并读回核对。
3. 该组只把新旧归档资料的差异整理为 `pending_review` 候选，不批准、不发布，正式版本保持不变。10月21日A6运行的结果、失败轮次和回退见附录7.5及对应证据。

导入结构兼容不等于真实业务复现；未取得接收机器的实际运行记录前，不据此声称异机复现通过。
