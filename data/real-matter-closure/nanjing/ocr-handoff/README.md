# 南京扫描公报OCR交接

OCR由队友处理。本目录只提供原件绑定、输出契约和下游验收边界，不执行OCR，不补造识别文本。

请求见 `request-v1.json`；原件已在相邻的 `review-pack-v1/originals/law-gazette-2011-pages651-654.pdf`，由政府网站取得并封存在规则复核包中。必须先核对请求内的SHA256和字节数，处理全部四个PDF页面；PDF页码从1开始，不与纸面印刷页码混用。

沿用仓库已有 `gov_mcp.knowledge_pdf.validate_ocr` 契约。返回UTF-8 JSON：顶层 `schema:1`、`engine:"windows.media.ocr"`、`language`、`input_sha256`、`confidence_available:false`、`requires_visual_review:true` 和 `pages`。每页仅含 `page`、原始 `text`、`line_count`、`width`、`height`，页号必须连续完整；几何和行数限制按现有校验器执行。不得编造置信分数或静默跳过空白/失败页，无法识别时返回明确失败，保留原件供处理。

下游将先校验原件绑定、语言、全页顺序和结构，再保存原始逐页识别文本及可检索派生文本。识别到的法律条文只能进入待复核候选，不能把OCR文本等同官方电子原文、真人复核或正式业务发布。日期、数字和“交回原证”等关键字须逐页对照图像，错误不能靠模板自动补齐。

已有AI视觉记录只检查PDF第1、2页；它不是OCR输出，也不是独立人工批准。当前未收到本请求的真实OCR返回，**下游样本联调仍待完成**。
