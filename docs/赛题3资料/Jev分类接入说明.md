# Jev 分类与自动分流使用说明

更新：2026-10-15。

2026-10-18 当前路线已确定为 [Jev 官方 API](Jev官方API接入说明.md)：配置和工作台端口原已官方直连，本轮将独立实测脚本也改为默认官方接口，使用 `TYPESAFE_API_KEY`，新结果与旧 Vercel 证据分开保存。下方缺密钥、测试数量和网关状态按各自历史日期解释；本轮未真实调用。

2026-10-17 更新见 [Jev 分类判断优化](Jev分类优化说明.md)：当前默认合并三维判断并按请求预算分组，新增模糊类别与用途冲突复核。下方首版验收、密钥状态和分开请求描述属于历史记录；当前网关访问状态见 [Vercel 接入记录](Vercel-Jev接入与实测.md)。

现在可以在资料提取文字后，调用 Jev 给资料打标签，并把原文片段送到不同整理流程。现有 Nexent 查询继续覆盖全部原文。分类结果是机器候选，不是确认过的政务规则。

## 一 当前完成情况

| 项目 | 当前结果 |
| --- | --- |
| 三个分类维度 | 已实现资料类型、业务领域、片段用途；标签和边界说明可修改 |
| 官方接口 | 已实现 HTTP 请求、响应校验、版本记录、限流重试及异常转待复核 |
| 自动分流 | 已实现条款、指南、表单、公告问答四类片段包，以及待复核区 |
| 混合资料 | 可涉及多个领域；同一片段可进入多个流程，保留相同编号 |
| 准备入口 | 已增加可选分类开关，位置在文字提取之后、向量处理之前
|
| 离线检查 | 9 个工程样例的正序、倒序选项回放均通过；176 项回归通过 |
| 实际缺密钥运行 | 三份模拟资料的 9 个片段全部待复核，明确报缺密钥；向量处理继续完成 |
| 原查询链路 | 四项实际 MCP 查询复查通过，既有快照和资料版本保持不变 |
| 真实 Jev 效果 | **尚未测量，本机未配置 TYPESAFE_API_KEY，没有向 Jev 发送请求** |

离线回放用人工指定的返回值检验程序，并没有让 Jev 预测答案。它不能证明中文分类准确率、选项顺序稳定性或成本收益。

## 二 分类后去了哪里

| 结果包 | 整理内容 |
| --- | --- |
| `policy_clauses` | 政策规范及标为规范条款的原文片段 |
| `guide_sections` | 办事指南，以及条件、材料、步骤、部门、费用、时限片段 |
| `form_fields` | 表单，以及签字、字段和填写要求片段 |
| `notice_faq` | 通知公告和问答资料片段 |
| `pending_review` | 未知、其他类型、把握不足或处理失败的片段 |

片段包保存原文、定位、资产编号、片段编号、来源编号、原文件指纹和用途标签。组内不重复保存同一片段；跨组出现相同片段属于正常分流，通过片段编号关联。

这一步整理的是原文，还没有自动生成知识实体、关系、正式条款解释或业务结论。人工复核页面和知识候选发布仍在后续阶段。

## 三 怎样运行

在项目根目录打开 PowerShell。只分类已有解析资料：

```powershell
.\.venv\Scripts\python.exe scripts/classify_topic3_data.py
```

输入为 `.runtime/preparation/run/assets.json`。命令显示结果目录和不包含原文的汇总；调用失败时退出码为 2，片段仍保存在待复核包中。

从文字提取开始，同时分类和生成向量：

```powershell
.\.runtime\preparation\.venv\Scripts\python.exe scripts/prepare_topic3_data.py --generate-demo --classify --embed
```

完整准备入口也支持 `scripts/setup_topic3_preparation.ps1 -SkipModelDownload -Classify`。在已有查询演练上运行 `scripts/start_asset_query.ps1 -Classify`，则先分类已有解析资料，再启动原有查询演练。省略 `-Classify` 不调用 Jev。

分类失败不会被算成分类成功，但不会阻止本地解析和向量处理。配置本身错误时，汇总记录 `CLASSIFICATION_STAGE_FAILED`，原文仍留在解析结果中，等待修正后重跑。

从 [TypeSafe 官方控制台](https://console.typesafe.ai/) 取得 API 密钥，在运行命令的同一个窗口设置进程环境变量。以下方式隐藏输入，避免把密钥直接写入命令历史：

```powershell
$taskSecureKey = Read-Host '输入 TypeSafe API 密钥' -AsSecureString
$taskKeyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($taskSecureKey)
try {
    $env:TYPESAFE_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($taskKeyPointer)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($taskKeyPointer)
    Remove-Variable taskSecureKey, taskKeyPointer
}
```

密钥保留在当前进程环境，关闭窗口后失效。用完可执行 `Remove-Item Env:TYPESAFE_API_KEY`。不要写入配置、文档或 Git。

## 四 分类规则与接口

配置在 [classification.json](../../config/preparation/classification.json)。三个标签表为 `document_types`、`domains` 和 `purposes`；`routing` 决定送到哪个整理流程。新增标签时使用稳定英文编号，并写清中文定义和边界。保留未知类别，修改时更新 `rules_version`；程序另算完整配置指纹，防止漏记规则变化。

首版固定 `jev-1.13.0`，调用官方 `POST https://api.typesafe.ai/v1/systemone`。资料类型采用 Choice，领域和用途分别采用多个 Noul。长文按片段边界分窗口完整处理，不静默截断；窗口可能类型不同，因此长文不强行合成一个总类别。

接口在 `src/gov_mcp/classification.py`：

- `DecisionProvider.evaluate(state, questions, model)` 封装模型调用；更换服务时实现适配器，将结果映射成统一的 Choice/Noul 决策结果。
- `classify_corpus(corpus, config, provider=None)` 接收资产和片段，返回标签、调用状态、用量及原文片段包，不修改输入。
- `save_run(run, directory)` 创建新目录，保存完整结果、片段包和汇总。

每条记录包含原文编号、文件及内容指纹、定位、标签与概率、模型版本、规则版本、处理状态、分流去向和待复核原因。Noul 没有单独的官方置信度，因此该维度的 `confidence` 为 null。

默认 Choice 置信度至少 0.8；Noul 概率至少 0.8 为正标签，不超过 0.2 为否定，中间范围待复核。任一维度不确定时，整个片段暂不自动分流。**这些阈值不是正确率保证**，后续需用人工标注的中文资料调整。

超时 30 秒。429 和 5xx 最多重试一次，尊重可解析的 Retry-After；需等待超过 5 秒时转待复核，留待下次运行。认证失败、无效请求、超时和非法响应不盲目重试。不保存服务错误正文或密钥。响应必须符合标签集合、概率范围及总和、问题类型和固定模型版本。

首轮仅接受 synthetic 资料；public、private 或分类缺失都在请求前阻断。以后接入真实资料需另行实现范围核查和相应访问控制。

## 五 查看结果与验证

每次结果在 `.runtime/preparation/classification/<运行编号>/`：

- `classification.json`：完整分类记录、文档窗口判断及调用记录。
- `bundles.json`：按流程整理的原文片段，含待复核区。
- `summary.json`：数量、待复核比例、错误码、用量及耗时。

用量只统计成功且通过校验的响应，失败重试也可能计费，不能当作账单。分类单独保存，不覆盖历史检索快照。准备流程的公开汇总现在按运行时间创建新目录，保留旧记录。

```powershell
# 不联网：人工返回值回放，仅验证接口及分流
.\.venv\Scripts\python.exe scripts/verify_classification.py

# 联网：需要密钥，对模拟样例做真实分类和选项倒序比较
.\.venv\Scripts\python.exe scripts/verify_classification.py --live

# 原平台运行时，核查四种 MCP 查询和快照完整性
.\.venv\Scripts\python.exe scripts/verify_classification_retrieval.py

# 自动回归
.\.venv\Scripts\python.exe -m pytest -q
```

样例在 [classification.examples.json](../../config/preparation/classification.examples.json)，覆盖混合领域、信息不足、OCR 错字和诱导文字。真实调用检查分别记录三个维度与预期标签是否一致、分流差异、选项倒序差异、待复核比例、耗时和用量。九个样例是开发检查集，不是独立业务验收集。

本次证据在 `evidence/jev-classification/`：`contract-replay-56d2f2357ae846b8acd23f72dedf02bf.json` 为离线回放，`preparation-without-key.json` 为缺密钥后仍完成向量处理的记录，`retrieval-regression.json` 为四项实际 MCP 查询，`regression.xml` 为 176 项测试结果。

## 六 官方资料与下一步

依据：[产品介绍](https://docs.typesafe.ai/introduction)、[接口](https://docs.typesafe.ai/api)、[模型与语言支持](https://docs.typesafe.ai/models)、[置信度](https://docs.typesafe.ai/confidence)、[已知限制](https://docs.typesafe.ai/model-jaggedness/jev-1.13)。核查于 2026-10-15。官方说明英语是主要训练语言，中文效果必须另行测量。

下一步配置密钥后执行真实模拟样例检查，查看容易混淆的类别，再补人工复核和知识候选提取。是否节省整理时间、是否改善检索和知识抽取，需要后续对照结果支持。
