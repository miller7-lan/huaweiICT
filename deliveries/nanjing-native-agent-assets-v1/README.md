# 南京原生 Agent 配置参考包 v1

来自本机 Nexent 实际只读导出，含三个禁用草稿的完整提示词、Qwen 显示名与参数、角色关系、精确 MCP 服务绑定和实际导出的 Skill。租户、用户、Agent 和模型数字 ID 已去除。原始导出留在私有运行目录。

这不是 Nexent 原生导入 API 的请求体。不要将 restore-spec.json 上传到 /agent/import：当前上游通用导入会自动发布 V1，同名 MCP 工具按名称和 source 匹配，且模型数字 ID/参数键不能安全跨平台搬运。

恢复前先取得项目 feat/real-matter-closure 的完整源码，按 docs/南京事项本地原生编排.md 和 scripts/prepare_matter_native.py、scripts/provision_matter_native.py 的帮助说明部署。核对目标平台注册模型显示名，启动带鉴权的本地 MCP 后，以 --protocol dataflow-v4 创建本轮专用禁用、未发布草稿；逐项与本包比对模型参数、提示词、三个角色关系及工具 usage=govmatter_native_dataflow。不要导入旧租户 ID，也不要复用旧活动数据库或重置原有配额。实际模型运行必须采用新建、明确有界的活动。

本包仅提供恢复配置与完整性核对；没有在新 Nexent 平台执行导入、冷恢复或模型推理。Qwen v4 在原平台的真实成功轨迹位于 evidence/real-matter-closure/native-dataflow-v4/。规则、关系、答案仍是候选，人工复核、独立业务评测和正式发布未完成。正式业务发布关闭。

manifest.json 记录各文件 SHA256 和规范化配置摘要。可用 gov_mcp.matter_agent_assets.verify_bundle(Path(...)) 离线核对；本包本身不能证明来源真实性或新平台运行成功。完整项目 v2 ZIP 是冻结快照，不包含这次新增封装工具，请另取当前分支增量。
