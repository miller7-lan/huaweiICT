# 自由问答与知识库草稿接线说明

这两份 JSON 由本机 Nexent 原生导出接口取得，分别为字面检索问答草稿和原生知识库问答草稿。两者均禁用、未发布，限河南冻结历史资料解释；不据此判断现行政策、个人资格或实际应缴金额。

## 文件与外部依赖

| 文件 | 内容 | 接收机器需要重绑 |
| --- | --- | --- |
| `qa-agent.json` | 字面检索草稿，本机编号38 | `govkb_frozen_qa`服务、`govkb_frozen_retrieve`和`govkb_frozen_verify`、接收方模型 |
| `kb-agent.json` | 原生知识库草稿，本机编号39 | `knowledge_base_search`、`govkb_frozen_verify_corpus`、接收方知识库索引与模型 |
| `frozen-knowledge-20261105.zip`（本机位于`artifacts/knowledge-20261105/`，作品附件中位于“知识库”目录） | 7份按来源导出的Markdown、581个分块的定位与散列清单 | 匹配原件、合法使用范围、本地嵌入模型与存储绑定 |

知识库含7份公开政务资料的冻结文本，保留原网址、修订、散列与定位；政府原文权利归发布机关，随作品提交仅供评审复核，不在公开仓库和公开代码ZIP中再分发。JSON内的本机索引、模型和工具编号不能直接当成新机器编号；不携带账号、MCP Bearer或云端API密钥。

## 新机器操作顺序

1. 按项目复现指南准备固定版本Nexent、已获准的来源副本和接收方自己的模型配置。核对来源散列，构建`.runtime/frozen-qa-20261030/corpus.json`。
2. 使用随附新版代码和散列匹配的原件，运行 `python scripts/build_frozen_corpus.py --offline --output-runtime .runtime/frozen-qa-20261113-linked`，生成 591 单元的自由问答语料。再运行 `python -m gov_mcp.frozen_qa_server --corpus .runtime/frozen-qa-20261113-linked/corpus.json`。38 号草稿通过原 MCP 工具取得新证据。新增的 10 个单元是网页标题和日期，39 号草稿沿用下述 581 块原生知识库。令牌只保存在接收方私有目录。
3. 按`provision_topic3_embedding.py`的说明准备本地`qwen3-embedding:0.6b`，确认调用的是接收机器本地模型。
4. 执行`python scripts/export_frozen_knowledge.py`和`python scripts/provision_frozen_knowledge.py --apply`。本部署缺文件解析服务，故采用逐单元手工分块；581块首次本地嵌入约50分钟，不反复重跑。
5. 执行`python scripts/configure_frozen_kb_ui.py --apply`选择已存在的本地默认向量模型；遇到其他已选默认值会停止。执行`python scripts/bind_frozen_knowledge_files.py --apply`把7份Markdown上传至该知识库的本地存储并绑定分块。原来源URL保留在元数据，仅更新存储路径，不改原文、定位或向量，不绕过权限检查。
6. 执行`provision_frozen_qa_native.py --apply`和`provision_frozen_kb_native.py --apply`，由脚本按当前服务、模型及索引接线。也可导入JSON后逐项重绑；须读回验证，不能只看导入成功提示。
7. 核对草稿禁用、未发布、范围停止与精确引用检查，再按新机器的单轮授权执行公开归档问题。真实业务复现及正式30题盲评分别记录，单条知识库演示不计入独立成绩。

## 本机已取得的证据

- `evidence/frozen-qa-20261030/nexent-knowledge-readback.json`：7份资料、581块、1024维本地嵌入及3条检索探针。
- `kb-ui-default-20261105.json`：仅选择既有本地向量模型，其他默认设置、应用设置与39个智能体保持原值。
- `kb-file-binding-20261105.json`：本地存储路径与逐文件预览检查，581块原文、标题及文件名保持。
- `native-kb-run-20261105T010357Z.json`：知识库检索、MCP核验、作答共3次模型启动、约11.3秒；仅为接线冒烟。数值和引文的确定性核验不能代替全部语义及业务正确性的人工评价。

上述相对证据文件位于同一`evidence/frozen-qa-20261030/`目录。私有SSE、会话和令牌不在本配置目录。
