# Jev 英文翻译层

日期：2026-10-18。

## 处理顺序

中文原文 → DeepSeek 官方接口翻译 → Jev 英文分类或支持判断 → 按原片段编号关联中文结果。

默认分类配置已启用英文适配器。它翻译资料标题、正文、候选名称与引用、判断问题及类别定义；片段编号、来源编号、标签键、模型名、哈希、定位和路由保持原样。Jev 返回的有限标签直接关联原资料，中文原文仍进入原来的分流包，不把英文译文当作正式政策原文。

翻译提示要求保留否定、例外、条件、日期、数量、单位和引用，不执行资料中的指令、不摘要、不补事实。程序另外检查返回字段完整、译文非空、不残留中日韩统一表意文字，以及阿拉伯数字及其出现次数保持一致。这些工程检查不能证明翻译语义完整，尤其不能自动证明否定、政策术语或适用范围翻译无误。

## 接入位置与开关

- `config/preparation/classification.json`：规则版本 `gov-classification-3`，`translation.enabled: true`，供应商固定 `deepseek_official`，目标语言固定 `en`。
- `src/gov_mcp/jev_translation.py`：复用现有 DeepSeek 官方凭据和传输，使用 JSON 翻译结果，输出预算为 8192 tokens；其他 DeepSeek 角色仍保持原来 1024 的默认预算。
- `classify_corpus` 默认供应商工厂及资料准备脚本自动使用英文适配器；知识维护引擎的默认分类与候选原文支持核验共用该适配器。连接端口在真实 Jev 供应商路径启用同样处理。
- 显式注入的测试回放或自定义供应商保持调用者提供的行为；要对其做英文输入验证，使用 `with_english_input(provider, config, translator=...)` 或 `EnglishJevProvider`。本轮自动测试使用注入翻译结果与本地 HTTP fixture，没有真实推理。

翻译沿用 `config/decision/connections.json` 中现有的 `inference_enabled` 开关，当前仍为 `false`。本轮只完成接线和离线验证，没有开启真实 DeepSeek、Jev 或收费心跳。开关关闭时，如果已有 Jev 凭据但需要翻译，会报告 `TRANSLATION_INFERENCE_DISABLED` 并转待复核；不会改成中文继续请求。缺少 Jev 凭据时优先报告 `MISSING_API_KEY`，避免先花费翻译用量。翻译凭据缺失、错误或输出截断统一报告 `TRANSLATION_UNAVAILABLE`。

将 `translation.enabled` 改为 `false` 可进行中文输入对照。`scripts/test_jev_live.py --live` 仍是原有直接 Jev 测试，明确标记不含翻译、不会调用 DeepSeek；它不能用于证明英文链路已实测。未来真实对照需分别记录中文直连、英文翻译及 Jev 用量与质量。

## 缓存、大小和留痕

同一适配器实例对重复原文、问题和类别定义复用译文，最多保留 512 条内存缓存，进程重启即失效。翻译请求按原字符串去重，通常每批不超过 3000 字符；单条较长文本完整发送，不从中截断。缓存记录实际翻译模型，命中缓存时仍可追溯模型名。

英文输入重新检查现有请求字节预算。超出预算报告 `TRANSLATED_INPUT_TOO_LARGE`，进入待复核并等待重新分块，不静默删减内容。翻译结构错误或残留中文报告 `TRANSLATION_INVALID_RESPONSE`；数字发生变化报告 `TRANSLATION_NUMBERS_CHANGED`。失败结果不进入缓存。

`.runtime/preparation/classification/<运行编号>/classification.json` 的调用记录包含 `english_input`，可按其中原片段编号核对 Jev 实际收到的英文正文和问题；中文包继续保存原文。翻译回执仅存版本、模型、源与译后指纹、请求字节、缓存命中和用量，不在诊断中输出原文或服务错误正文。英文正文同样属于资料内容，只保存在原有私有结果边界内。

汇总的 `translation` 独立记录翻译调用、实际模型和 tokens；原 `usage`、`http_attempts` 继续仅统计 Jev，不能把翻译费用混入 Jev 节省比例。失败请求仍可能计费，记录不是账单。

## 验证边界

本轮新增自动检查覆盖英文正文与规则输入、逐片段关联、原文完整性、候选与证据共同翻译、缓存模型追溯、隐私与推理开关、缺凭据、错误输出、数字变化、截断和译后超限。可运行：

翻译专项 **28 项通过**，完整回归 **427 项通过**，有 3 条既有依赖警告；见 [回归记录](../../evidence/jev-translation/regression.xml)。所有语言模型响应均为离线 fixture。

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_jev_translation.py -q
.\.venv\Scripts\python.exe -m pytest -q
```

翻译层旨在让 Jev 使用英文判断，实际是否提高本项目准确率尚未测量。仍需用同一批独立中文标注资料比较中文直连与翻译后结果，并单独检查翻译引入的错误、待复核比例、端到端延迟与总费用。此前 Jev 网关权限问题也尚未因本次接线得到验证或解决。
