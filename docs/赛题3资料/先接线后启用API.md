# 先接线，最后启用 API

更新：2026-10-17。对应决定：先连接各组件，模型 API 放到最后一步。

本轮已把 **本机 Qwen 服务、Nexent、工作台 MCP、六个 Agent 草稿及 Skill** 实际连接起来。Jev 请求适配器已准备，凭据引用为空，未请求 Jev 服务。模型推理开关关闭；没有生成请求，也没有把原有 Agent 换成新的草稿。

## 已接好的部分

| 部分 | 当前实际状态 |
| --- | --- |
| Qwen 服务 | 本机 Ollama 可访问，8B 与 4B 模型目录已核对；只读元数据，未加载模型推理 |
| Nexent 配置与运行服务 | Docker 已启动，两个服务实际健康检查通过 |
| 新 MCP 服务 | `govflow_workbench` 已登记；平台实际扫描到 12 个可用工具 |
| 查询主控 / 检索 / 条件核验 | 已创建草稿并绑定工具和子 Agent；模型选择为空，禁用，无发布版本 |
| 更新主控 / 候选整理 / 更新核验 | 已创建草稿并绑定工具和子 Agent；模型选择为空，禁用，无发布版本 |
| Skill | `govkb-workflow-wiring` 已上传并绑定两个主控草稿 |
| Jev | 固定 `jev-1.13.0` 的请求、校验、缺凭据处理已接在传输适配器中；凭据未绑定、未联网调用 |
| 工作台 | 新增“连接与接口”，可检查本机服务和查看各角色接线状态，不触发模型 |

本机新 Agent ID：检索 14、条件核验 15、查询主控 16、候选整理 17、更新核验 18、更新主控 19；Skill ID 为 4。ID 属于这次本机实例，换环境以 `.runtime/agent-workflow/native-drafts.json` 为准。平台的旧 Agent 未被这些新 ID 覆盖。

Nexent 的导入接口会自动尝试发布 V1。本轮特意使用原生“创建／更新草稿”接口，所以六个新 Agent 的发布版本数确实为 0，而不是只在文件里写“未发布”。再次运行接线脚本会验证名称、禁用、空模型和无发布版本，避免覆盖已经启用的 Agent。

## 查询现在怎样接到子 Agent

新增了可由原生子 Agent 分步调用的真实程序工具：

1. 主控调用 `govkb_open_query`，固定知识版本并建立任务。
2. 检索子 Agent 调用 `govkb_retrieve_query`，读取登记原文。
3. 核验子 Agent 调用 `govkb_verify_query`，程序检查引用和三值条件。
4. 主控调用 `govkb_query_state` 读取真实进度，缺依据时补查，最多三轮。
5. 主控调用 `govkb_finalize_query`，程序再次检查当前冲突暂停，保存并返回答案。

实际 MCP 协议已经跑通这条链路，任务、每轮结果、原文和版本都有持久化记录。这一轮由协议客户端调用程序工具，**不是让 Qwen 扮演主控和子 Agent**；草稿中的原生调度提示词已经绑定，真实多模型调度留在最终启用时验收。

更新侧延用已经存在的候选包与程序核验接口。原生草稿目前对工程回放候选进行读取、确认和核验；不能把这种幂等确认称为已经完成真实模型语义抽取。真实模型的新输出只能成为候选，仍需出处校验和专业人员发布。

## API 最后一步之前保留什么

`config/decision/providers.template.json` 中原有七个空槽位继续保留。

新增的 `config/decision/connections.json` 登记路由、已知模型名和本机地址，但：

- `inference_enabled` 为 `false`。
- 所有 `credential_ref` 为 `null`，没有 API Key。
- 六个原生草稿的 `model_ids` 均为空，状态为禁用。
- 没有调用 `/api/chat`、Jev `systemone` 或 Nexent `/agent/run`。

`Connections` 提供 Qwen JSON、Jev SystemOne 和 Nexent SSE 传输接口，默认在发送请求之前拒绝未启用调用。它们的网络请求形状、返回校验、错误处理、串行执行及 Qwen 阶段结束释放显存参数，使用明确的 HTTP 模拟验证。真实健康检查与真实 MCP 工具检查另存，不能混称模型联调。

工作台现有按钮继续运行确定性离线流程。后续启用原生模型时，还要选择并绑定模型、按子 Agent 到主控的顺序发布，然后验证真实工具轨迹与结果；不能只改一个显示标签就当作启用成功。

## 怎样查看与复现

打开 [本机工作台](http://127.0.0.1:8780/)，进入模拟专业人员入口，选择“连接与接口”，点击“检查连接（不调用模型）”。本机口令沿用私有 `.runtime/agent-workflow/access.json`，无需新增账号或填写模型 Key。

```powershell
# 服务已启动时，重新登记／核对六个禁用草稿；不导入发布、不调用模型
.\.venv\Scripts\python.exe scripts\provision_agent_workflow.py

# 检查真实平台登记、草稿、子 Agent、Skill 和分阶段 MCP 程序链路
.\.venv\Scripts\python.exe scripts\verify_workflow_connections.py

# 回归；模型传输测试使用 HTTP 模拟，平台联通实测另见上一个脚本
.\.venv\Scripts\python.exe -m pytest -q --junitxml=evidence/agent-workflow/regression.xml
```

接线实测结果位于 `evidence/agent-workflow/connections-verification.json`：15/15 项通过，6 个禁用且无模型的原生草稿、12 个 MCP 工具、Skill 与子 Agent 绑定均从真实平台读回核对；模型推理请求数为 0。这与原有 21 项模拟生命周期演练分别保存。完整回归为 335 项通过，本轮新增 18 项连接与传输契约检查；浏览器连接页已实际操作并保存 `connections.jpg`，未出现前端错误。

Jev 密钥绑定、真实中文分类效果、Qwen 实际输出、新 Nexent 多 Agent 推理，以及真实政务知识的专业验收，均仍保留为最后启用后的验证内容。
