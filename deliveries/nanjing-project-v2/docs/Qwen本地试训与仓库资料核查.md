# Qwen 本地试训与仓库资料核查

2026-10-16。本轮先训练本地 Qwen3-4B 识别咨询问题主题，用小规模实验验证训练链路。政策原文、现行适用关系和业务发布仍由知识库管理。

## 本轮实际结果

已完成真实 GPU 训练，运行编号 `20261016T141737129121Z`。

| 检查项 | 实际结果 |
| --- | --- |
| 参数更新 | 完成 8 个优化步骤，更新 2,949,120 个 LoRA 参数 |
| 训练前诊断 | 7 题中 4 题与草拟标签一致 |
| 训练后诊断 | 同样 7 题中 5 题与草拟标签一致 |
| 逐题变化 | KQ06 由范围证据改为材料身份；KQ05、KQ26 仍与标签不一致，其余保持 |
| 保存检查 | 144 个适配参数张量均为有限值，LoRA B 已非零，全部文件指纹一致 |
| 重新加载 | 已重新加载保存的适配器，本地输入“身份证换领需要交多少费用？”返回 `fees_time` |
| 本轮总用时 | 约 12 分 34 秒，含读取/校验权重、前后诊断及训练，不含环境安装和模型下载 |
| 显存指标 | PyTorch 峰值已分配约 4.31 GiB；系统观察到的 GPU 总占用约 7.3 GiB，两个口径不同 |
| 部署状态 | 实验适配器独立保存，未替换原 Ollama/Nexent 模型 |

结果见[完整运行记录](../evidence/qwen-training/run-20261016T141737129121Z.json)与[保存产物核查](../evidence/qwen-training/artifact-check-20261016T141737129121Z.json)。最后一步损失约 0.107，中间存在波动；损失不等于业务准确率。七题仍是 AI 标签开发诊断，不能用 5/7 声称独立测试准确率或训练收益已经确立。没有继续对七题反复调参追分。

适配器及分词器保存在 `.runtime/training/runs/20261016T141737129121Z/adapter/`，整个目录约 26.4 MiB。

重新加载记录见[本地推理检查](../evidence/qwen-training/inference-smoke.json)。本轮相关工程回归为 216 项通过，另有 3 条既有依赖警告。

## 仓库更新在哪里

新增资料位于 `origin/feat/m2-minimal-flow`，本轮冻结提交为 `4d0a31e9a7eb9db2847c4eea0a0e48a8063f29c8`。本机仍保留 `codex/sync-m1-m2` 及尚未提交的心跳开发内容；本轮通过 `git show` 读取指定提交，没有执行合并或重置。

该提交的候选目录记录了 37 个来源、33 条资料归档记录、83 个修订、261 个当前片段，正式知识版本和发布头均为 0。这些数量不等于独立有效政策数量，也不等于可用于训练的样本数。共享目录主要包含网址、定位、分类和原件指纹，完整原文及私有数据库未随目录导出。

仓库另有 30 道开发题、30 份 AI 参考草案和三轮模型试验记录。最近一轮是程序 13/30、AI 内容评阅 6/30、两者同时通过 4/30，属于已经参与开发的题目。没有把这些数字当作独立业务准确率，也没有把失败模型答案加入训练目标。

可以查看原始资料：[冻结候选目录](https://github.com/miller7-lan/huaweiICT/blob/4d0a31e9a7eb9db2847c4eea0a0e48a8063f29c8/data/knowledge/candidate-catalog.json)、[最近一轮核查记录](https://github.com/miller7-lan/huaweiICT/blob/4d0a31e9a7eb9db2847c4eea0a0e48a8063f29c8/evidence/knowledge-update/retrieved-model-trial-integrity.json)。

## 第一轮具体训练什么

使用仓库开发题本身及已有机器草拟的主题标签，合并成四类：

| 输出标签 | 通俗含义 | 训练题数 | 诊断题数 |
| --- | --- | ---: | ---: |
| `materials_identity` | 材料、申请身份、照片与指纹等信息采集 | 7 | 2 |
| `process_channels` | 办理流程、地点和领取渠道 | 3 | 1 |
| `fees_time` | 收费、办理时长 | 1 | 1 |
| `boundaries_evidence` | 范围、日期效力、证据冲突、隐私、权限等 | 12 | 3 |

输入是一句话的问题，输出例如 `{"topic":"fees_time"}`。训练目标只包含这个主题标签，不包含参考政策答案或历史模型回答。

主题来自 AI 草案，合并映射由本轮配置定义，尚未经过独立人工标注。照片等相近问题按问题族分组，训练与诊断之间不共享同一问题族。23 条训练、7 条诊断都来自已经参与项目开发的 30 题；七题只能用于观察本轮训练变化。费用和时长各仅一题，样本尤其少。

这一步可以作为咨询入口分流的实验。它与 Jev 的“文档类型、业务领域、片段用途”三维分类承担不同任务，当前没有替换 Jev，也没有接入正式查询。

## 训练环境和文件

本机实查为 RTX 5060 Laptop GPU，8151 MiB 显存，约 16 GiB 内存。训练依赖单独安装在 `.runtime/training/.venv`，保留现有 Nexent、Ollama 和资料解析环境。

采用 Qwen3-4B、NF4 四位加载及 LoRA，只更新注意力中的少量适配参数；默认 rank 8、单样本、梯度累计 2、最多 384 tokens、8 个优化步骤。这是通路试验的规模，不是完成业务训练的标准。超过上下文的样本会报错，不静默截断。

权重提供两种来源：

- Hugging Face 官方 `Qwen/Qwen3-4B`，固定提交见配置。
- 本轮实际下载使用 [Qwen 官方 ModelScope 仓库](https://modelscope.cn/models/Qwen/Qwen3-4B)。每个文件的修订、大小和 SHA-256 均固定在 `config/training/qwen3-4b.modelscope.json`。不声称两个平台的修订编号相同。

Ollama 中已有的是推理用量化文件，训练使用另行下载的权重。生成的适配器单独保存，尚未转换、导入 Ollama 或发布到 Nexent。

| 文件 | 作用 |
| --- | --- |
| `config/training/qwen-topic-smoke.json` | 分类映射、问题族、模型和训练参数 |
| `scripts/prepare_qwen_training.py` | 按 Git 提交冻结共享资料，生成带指纹的数据集 |
| `scripts/download_qwen_training_base.py` | 下载官方权重，逐个验证大小和 SHA-256，可继续未完成下载 |
| `scripts/train_qwen_topic.py` | GPU 检查、训练前诊断、实际参数更新、保存适配器和训练后诊断 |
| `scripts/verify_qwen_training.py` | 检查保存参数、文件指纹和真实更新 |
| `scripts/predict_qwen_topic.py` | 重新加载保存的适配器，输入一条问题做本地分类 |
| `evidence/qwen-training/preparation.json` | 仓库审计及数据划分记录 |
| `evidence/qwen-training/run-*.json` | 每次尝试结果，包含阶段、步骤数、失败类型和真实指标 |

原始下载、数据快照、训练集、诊断集和模型适配器均位于 Git 忽略的 `.runtime/training`。训练前后用同一组诊断题，不把诊断问题或答案送入梯度计算。

## 复现操作

在项目目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup_qwen_training.ps1
.runtime/training/.venv/Scripts/python.exe scripts/train_qwen_topic.py --preflight
.venv/Scripts/python.exe scripts/download_qwen_training_base.py --direct-network
.runtime/training/.venv/Scripts/python.exe scripts/train_qwen_topic.py --local-base .runtime/training/base/Qwen3-4B-modelscope
```

使用已经完成的适配器进行一次分类：

```powershell
.runtime/training/.venv/Scripts/python.exe scripts/predict_qwen_topic.py --run-id 20261016T141737129121Z --question "身份证换领需要交多少费用？"
```

本机代理超时时，安装入口可以加 `-DirectNetwork`。它只影响该进程及其子进程，不修改系统代理。下载入口的 `--direct-network` 同理。训练使用已经验证的本地权重，无需外部模型 API，也不上传项目样本。

若选 Hugging Face 来源，训练命令改为 `scripts/train_qwen_topic.py --download`。不带下载选项且未指定本地权重时只读缓存。每次运行新建独立结果目录，失败也保存记录；`training_completed=false`、`optimizer_steps=0` 不能解释为已训练。

训练完成须同时检查实际优化步数、有限损失、适配参数确实改变和适配器文件指纹。七题的逐题输出同时保存，结果可能没有改善；不自动替换当前模型。

## 下一步怎样用真实政务资料训练

1. 从资料维护端导出带原文、网址、原件指纹、修订和定位的片段快照。当前共享目录不能独立还原完整原文。
2. 修复引用选择接口：模型选择已有片段编号及原文区间，由程序提取引文；禁止凭空编编号或近似修补引用。
3. 按真实原文重做并核对“问题、实际检索上下文、期望回答或工具动作”。明确区分申请与领证、文件原文事实与本地现行适用，保留冲突及缺证据状态。
4. 对已经核对的样本另建回答行为 LoRA 训练集；把事项、来源修订及相近问题族一起分组。重新准备没有参与开发的留出问题。
5. 比较原 4B、适配后 4B 和现有检索工具链的表现，检查分类、原文引用、无依据停止及模型资源占用，再决定是否接入 Nexent。

政策变化继续走知识库心跳、版本和复核流程。不要靠每次微调记住最新政策，也不要把训练出的确定语气当作来源现行适用的确认。

技术依据：[Qwen 官方训练示例](https://github.com/QwenLM/Qwen3/blob/main/docs/source/training/unsloth.md)、[PEFT 量化训练说明](https://huggingface.co/docs/peft/main/developer_guides/quantization)、[bitsandbytes 安装与 GPU 支持](https://huggingface.co/docs/bitsandbytes/installation)、[PyTorch CUDA 安装版本](https://pytorch.org/get-started/previous-versions/)。
