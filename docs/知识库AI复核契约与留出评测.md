# 知识库AI复核契约与留出评测

2026-10-16。用户已明确将资料核对、材料分析和参考答案工作交由Codex接手。知识库现在支持如实记录AI或人类评阅者；当前本机运行库已显式升级为 `schema=2 / review_contract=attributed_v2`。没有生成通过报告、正式版本或发布头。

## 两种契约及共同质量要求

通用初始化仍保留原 `schema=1` 策略，旧库与旧报告按原规则读取；既有库不会因代码更新被静默改策略。新契约需通过本地维护命令显式升级，保留旧策略原件，检查操作前策略/开发题登记指纹，并拒绝在已有正式发布头时切换。升级与发布使用SQLite立即写事务互斥，防止在检查零发布后被并发发布穿过。

两种策略的门槛相同：至少30题、正向题平均Recall@5不低于90%、断言支持率100%、范围/失效规则/关键材料等错误计数为0，非空语料同时覆盖回答与停止；原件、修订、来源、快照、策略、访问状态和新鲜度检查继续执行。`engineering_fixture` 仍只能用于明确的模拟资料。

## 新报告需要保存什么

根字段包括 `schema=2`、`status=reviewed`、`dataset_role=independent_business_acceptance`、`version`、`snapshot_binding`、`cases`，以及下列信息：

| 字段 | 具体要求 |
| --- | --- |
| `reference_author`、`reviewer` | `kind` 为 `ai` 或 `human`，填写真实执行者 `name`；AI还需 `model`、`run_id` 和显式的 `model_revision`，未知版本填 `null` |
| `human_review_completed` | 必须与评阅者的 `kind` 一致；AI评阅不能填真人已完成 |
| `reference_frozen_at` | 带时区；不晚于实际测试开始时间 |
| `observed_run` | 实际运行ID、`executor`、带时区的 `started_at/completed_at`，以及 `reference_answers_in_model_input=false` |
| `reference_reviewed_at` | 带时区；测试完成后评阅，不能在未来，距检查不超过30天 |
| `reference_sha256` | 冻结参考内容、作者、冻结时间及快照绑定的指纹 |
| `observation_sha256` | 被测输出、取回定位、输入/轨迹指纹、运行信息及快照绑定的指纹 |
| `holdout` | `method=normalized_question_hash_disjoint`，与数据库完全一致且排序去重的 `development_question_sha256`，以及 `registry_sha256` |

每题继续保存 `id/question`、`reference_answer/observed_answer`、预期/实际回答或停止结果、四项错误计数、断言总数/支持数及标准/实际前五证据。新契约使用 `review_judgment=pass` 和具体 `review_notes`，另需 `trace_sha256/input_sha256`；拒绝混入旧 `human_judgment` 字段。提交命令中的评阅者名称必须与报告的 `reviewer.name` 一致。

指纹使用仓库统一的规范JSON或原始字节算法。可通过 `gov_mcp.knowledge_review.reference_fingerprint`、`observation_fingerprint`、`question_fingerprint` 计算；不能直接套用其他JSON序列化方式的哈希。旧报告不能只改状态或重算指纹成为新报告，必须具备实际冻结、输入、输出、轨迹和评阅证据。

## 开发题与留出题

数据库新增只追加的开发问题登记。问题经过NFKC规范化、去空白和大小写归一后计算指纹，防止改空白后重复使用同一题。登记清单也进入新快照绑定；增加开发题后旧评测须重新核对，不能省略已登记问题来避开重复检查。

当前已从两轮模型冻结清单、检索开发结果、参考草案及实际双角色范围/咨询检查等7份已知记录中整理问题，连同已使用的规范查询，共登记40个去重问题指纹。给定证据模型开发运行器会在后续新运行前登记问题。其他开发流程使用新题前也应执行登记命令。

哈希不同不能证明语义独立。改写旧题、使用同一答案提示、漏登记开发活动或实际输入泄漏参考答案，仍会破坏留出评测。执行者需要保存冻结清单和真实输入/输出，评阅者核对语义重合与泄漏；报告中的声明、指纹格式或AI身份字段都不能独立证明这些事实，也不能证明现行政策真实有效。本轮没有把原30题开发集改名为留出集。

## 维护和恢复

在仓库根目录运行；参数应来自实际检查，不要照抄过期指纹：

```powershell
.\.venv\Scripts\python.exe -m gov_mcp.knowledge_cli --runtime .runtime/knowledge-update register-development-questions <已用于开发的问题JSON> --source <实际来源记录>
.\.venv\Scripts\python.exe -m gov_mcp.knowledge_cli --runtime .runtime/knowledge-update upgrade-review-contract --expected-policy-sha256 <检查时的策略指纹> --expected-registry-sha256 <检查时的登记指纹> --maintainer "Codex AI" --confirmed
.\.venv\Scripts\python.exe scripts/export_knowledge_review_packet.py
```

登记文件格式为包含 `cases` 的JSON，每个条目有 `question`。维护命令不会接入群众/工作人员的只读MCP；这两端仍仅开放状态及证据查询。导出的私有复核包现在提供真实策略、开发题登记、通用执行者复核空字段和既有AI参考集，旧真人字段保持空白。

新策略和开发题登记已随完整数据备份在隔离目录恢复并核对。旧策略另保存在私有 `policy-history` 目录，升级前[完整备份记录](../evidence/knowledge-update/review-contract-pre-upgrade-backup.json)也已保留；升级后[完整备份](../evidence/knowledge-update/full-backup-restore.json)仍含21原件/27文件，未包含账号令牌或启动服务。

## 本轮证据与边界

[实际升级检查](../evidence/knowledge-update/review-contract-upgrade.json)11项通过：策略和阈值、40题登记、升级前后备份、恢复后的策略/登记、候选数量及服务心跳均核对。数据库仍为21份、60修订、124当前片段、0正式版本/0发布头，评测记录为0。

[完整回归](../evidence/knowledge-update/regression-tests.xml)461项通过，3条既有警告；[针对性检查](../evidence/knowledge-update/attributed-review-tests.xml)81项通过。所有新契约正向报告均为临时库中的自编模拟字段，未执行真人或AI业务验收；测试覆盖身份误标、参考/输出错绑、时序、开发题重用、质量门槛、策略切换、并发写入和完整恢复。第二轮真实模型开发结果仍为13题AI评阅符合、17题需改。

下一步处理来源用途、日期类型、试点适用关系及具体材料冲突，改善检索与答案支持，再准备真正未参与开发的评测。正式业务验收及最终GitHub同步仍未完成。
