# 政务空白核心链路：本地实施

目标是 Nexent **v2.7.0** 原生界面的三 Agent 技术链路，以及固定 Qwen3 4B / 8B 对照。当前已完成 MCP 工具、身份注入验证、部署与原生账号配置脚本、Agent 导入包、验收脚本。实际进度见 [实施记录](evidence/implementation-status.json)。Docker 引擎需要启用 WSL 2；原生账号、原生 UI 与 40 次链路验收须在引擎就绪后执行。

## 已有内容

| 文件 | 用途 |
|---|---|
| `src/gov_mcp/core.py`、`server.py` | 仅三个工具；真实空 SQLite；角色映射；独立 Bearer；SSE；实际调用审计 |
| `config/agents.nexent-v2.7.0.json` | 原生 Agent 导入包；主 Agent 4 步、子 Agent 3 步；只绑定各自工具 |
| `models/Modelfile.4b`、`.8b` | 固定基础标签与上下文 8192、温度 0.1、输出上限 1024 |
| `scripts/deploy.ps1` | 固定官方源码提交、官方 Docker 部署、MCP 容器、账号与 Agent 初始化 |
| `scripts/provision_nexent.py` | 创建隔离租户、账号、模型、SSE 连接、导入并发布 Agent，保存真实 ID |
| `scripts/benchmark_native.py` | 使用原生发布版本、真实登录账号，每模型 4 类 × 5 个新会话，保存 SSE 与工具审计 |
| `evidence/unit-tests.xml`、`sse-transport.json` | 已执行的自动测试和真实 SSE 工具证据 |

依赖安装在 `.venv`，锁定清单为 `requirements.lock`。Python 本机环境为 3.12；MCP Docker 镜像使用 3.11。Nexent 后端由官方镜像提供，不在本机 Python 中安装 SDK。

原生接口适配参考仓库的 [nexent-backend 技能](vendor/nexent/.agents/skills/nexent-backend/SKILL.md)，核对了 HTTP 身份与响应契约、配置约定及流资源生命周期；没有修改上游后端、SDK 或 SQL。

## 启动与恢复

首次下载此仓库时，先安装 Git、Python 3.12、Ollama 和 Docker Desktop，并执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

此入口创建本地 `.venv`、安装锁定依赖，并下载、核对 Nexent v2.7.0 的精确源码提交。`vendor`、虚拟环境、私有 `.runtime` 和模型权重不随仓库发布。若 Python 使用独立可执行文件，可传 `-PythonCommand 'C:/Python312/python.exe' -PythonVersion ''`。

1. 在**管理员 PowerShell** 执行 `wsl --install --no-distribution`，按 Windows 提示完成安装和重启。当前进程不具备启用 Windows 可选功能的权限。Docker Desktop 已安装在用户目录；启动它并确认 Linux 引擎可用。
2. 在本目录执行下列命令。下载脚本支持断点续传，校验官方 registry manifest 和每个 blob 的 SHA-256；不会关闭 Ollama 的重定向保护。

```powershell
.\.venv\Scripts\python.exe scripts\download_models.py
powershell -ExecutionPolicy Bypass -File scripts\configure_ollama.ps1
.\.venv\Scripts\python.exe scripts\verify_model_settings.py
powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1
```

`configure_ollama.ps1` 重启当前 Windows Ollama 安装的进程，设置单进程环境 `OLLAMA_NUM_PARALLEL=1`、`OLLAMA_MAX_LOADED_MODELS=1`、监听 `127.0.0.1:11434`，创建 `gov-qwen3-4b:ctx8192-v1` / `gov-qwen3-8b:ctx8192-v1` 两个固定参数别名。再次运行脚本即可恢复这些设置。每轮仅加载一个模型。所有推理请求明确传 `reasoning_effort=none`；模型检查会拒绝未关闭思考或实际上下文不符的情况。

Docker 内模型地址为 `http://host.docker.internal:11434/v1`。须在容器中验证可达性；如果当前 Docker Desktop 不能访问主机的 loopback，记录连接错误后先修复本机连接方式。不要直接将 Ollama 暴露给局域网来绕过故障。

官方脚本选择 `infrastructure,application,supabase`，关闭 Workbench，使用 lightweight 执行环境；没有资料解析服务、业务资产、业务资料导入。仅给官方 Compose 的宿主机发布端口增加 `127.0.0.1` 绑定，不修改服务或 SQL。Nexent 精确提交：`519b9b17c7f4cbc623256ac318ea96a20f33772e`。MCP 只挂载 `.runtime/mcp`，不挂载账号凭据或 Nexent 数据目录。

### 账号和权限

凭据保存在私有 `.runtime/accounts.json`，真实平台 ID 在 `.runtime/nexent-state.json`。**凭据文件生成不代表账号已创建。** 必须以原生 API 成功返回为准。Windows `.runtime` ACL 限制为当前用户和 SYSTEM；不要复制到知识库、提示词、对照证据或提交仓库。

| 账号 | 平台角色 | 工具业务角色 |
|---|---|---|
| developer | DEV | citizen（用于调试） |
| citizen | USER | citizen |
| staff | USER | staff |
| tenant_admin | ADMIN | 未映射（管理及负向验证） |
| super_admin | SUPER_ADMIN | 未映射（仅平台引导） |

后两个账号是官方权限体系要求的引导管理账号，不能代替两个独立 USER 测试账号。初始角色映射为空；初始化脚本只能根据登录接口取得的真实 tenant/user ID 写入 `.runtime/mcp/role-map.json`。普通用户的 Metadata 和聊天自称身份不能更改该文件。

### 工具行为和停止条件

每个结果保留 `status`、`items`、`citations`、`knowledge_version`、`message_code`，并附 `data.request_id` 关联实际审计。`knowledge_version=null` 表示没有业务知识版本。

- 配置正常且库为空：环境工具返回 `OK/ENVIRONMENT_READY`，附角色和资源计数。
- 检索空库：`EMPTY/NO_KNOWLEDGE`，记录、引用均为空，结束。
- 工作人员无事项、无材料：`BLOCKED/MISSING_CASE_AND_MATERIALS`，列明缺失项，结束。
- 群众调用工作人员工具：`BLOCKED/FORBIDDEN`；未映射身份：`BLOCKED/ROLE_NOT_CONFIGURED`。
- 业务库意外非空：`ERROR/BLANK_BOUNDARY_VIOLATED`，拒绝使用这些数据。
- 存储超时最多尝试两次；连接超时提示词要求最多重试一次。`EMPTY/BLOCKED/ERROR` 结果均不可通过重复业务调用“修复”。模型格式错误单独记录。

工具身份字段在 MCP schema 中声明，Nexent v2.7.0 的可信会话包装器移除模型可见参数，并覆盖模型提供的同名字段。MCP Bearer 是平台服务间连接凭据，模型不接触它。审计记录以 tenant/user 的 SHA-256 标识调用人，保留工具返回结果和耗时。

## 实际验收

先用三个账号在原生界面验证入口：开发账号使用调试界面；群众和工作人员账号分别登录 `http://127.0.0.1:3000`，在已发布主 Agent 点击“开始问答”。保留实际 UI 会话 ID、截图及调用轨迹。当前尚未执行 UI 验收，不能凭配置文件认定通过。

```powershell
.\.venv\Scripts\python.exe -m pytest -q --junitxml=evidence\unit-tests.xml
.\.venv\Scripts\python.exe scripts\verify_sse.py
.\.venv\Scripts\python.exe scripts\benchmark_native.py
```

原生对照脚本调用原生 UI 使用的运行 API，每轮先发布三个 Agent 的同一模型版本，再预热一次；预热不进入中位数。四类提示分别是群众空库自检、工作人员缺前提、群众自称工作人员并填入伪造 Metadata、重复群众自检。每类 5 个新会话，顺序执行。不要在采集期间人工触发其他会话，以免混入工具审计。

每次记录实际原生 SSE、子 Agent 起止、工具顺序与审计、最终结果 request_id、耗时、停止原因、显存和整机内存采样、业务计数。只有文字、没有真实工具轨迹，或最终结果无法关联实际工具请求，均失败。到达步数上限、运行错误、仍出现深度思考也失败。

只有某模型全部 20 个核心用例通过，才进入选择比较。仅一方通过选该模型；双方通过时比较预热后中位数，差距（以较快者为分母）不超过 10% 选 4B；双方均未通过不选择模型。原生 UI 和额外故障验收独立标记，核心性能比较不能替代它们。

额外验证需分别保留：可信身份覆盖、未映射账号、群众工具越权、技术超时停止。现有测试已经覆盖工具层和上游身份包装器，但没有声称已完成原生平台故障验收。故障只能模拟技术状态，不创建政策、案例、事项或材料。

本批只证明核心工程链路；不表示 BT01–BT16、业务准确率、算法效果或审批能力已经验证。
