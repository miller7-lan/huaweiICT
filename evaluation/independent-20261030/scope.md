# 30题独立评测范围（草案）

起草：陈子炫，2026-10-30（Asia/Shanghai）。**状态：草案，待程千城确认后冻结**；本文件不含题目或答案。流程依据[独立评测执行方案](../../docs/competition/独立评测执行方案-2026-10-21.md)，分工见[三人分工与修复计划](../../docs/competition/三人分工与修复计划-2026-10-30.md)。

## 范围

- **事项与地区**：河南省居民身份证的申领、换领、补领及收费，只涉及河南本省户籍。
- **评测目的**：评的是"按冻结的历史资料作答，或在依据不足时正确停止"，**不评现行政策效力**。题目可以问"某份资料怎么规定"，不能要求判断今天的应缴金额、实际办理时限或个人是否符合条件。这类问题应作为停止题。
- **资料范围**：只限下表 7 份公开原件。出题人和评阅人都按 URL 读原文；网页内容若与 SHA-256 对应的版本不一致，先记录差异，该来源暂不出题。

| 编号 | 来源 ID | 格式 | 公开 URL | 原件 SHA-256（前16位） | 目录状态 |
| --- | --- | --- | --- | --- | --- |
| A2 | `HN-FEE-CATALOG-XLSX-2025` | XLSX | https://xcoss.henan.gov.cn/typtfile/20250318/fdace0fca875409fa1338179dd218530.xlsx | `4aaddf9c385df36a…` | excluded |
| A3 | `HN-MEASURES-2021-PDF` | 扫描PDF | https://oss.henan.gov.cn/typtfile/20210601/c303a2a53f3f4396a25981f108532f9c.pdf | `db257f4c918d95d6…` | pending_review |
| A4 | `MOF-FEE-2007` | HTML | https://www.mof.gov.cn/gkml/caizhengwengao/caizhengbuwengao2007/caizhengbuwengao20078/200805/t20080519_26881.htm | `a77897e3863b5c61…` | pending_review |
| A5 | `MOF-FIRST-ISSUE-2018` | HTML | https://www.mof.gov.cn/gkml/caizhengwengao/wg2018/wg201805/201808/t20180820_2993673.htm | `1034a5595b3a5f5c…` | reviewed |
| A6 | `HN-MEASURES-2026` | HTML | https://hnga.henan.gov.cn/2026/05-11/3349863.html | `2fb241e0cd7b1f4d…` | reviewed |
| A7 | `HN-NORM-PUBLISHER` | HTML | https://hnga.henan.gov.cn/2022/08-16/2561597.html | `aae6df5924be7648…` | pending_review |
| B1 | `HN-MEASURES-2024-JY` | HTML | https://gaj.jiyuan.gov.cn/14190/15132/15148/t961601.html | `a4b1fb5ce282e2d8…` | excluded |

完整散列和修订号见 `data/knowledge/candidate-catalog.json`。目录状态是资料复核状态，不影响能否出题；`excluded` 只表示没有进入旧答复语料。

## 题型配比（建议，看到任何系统输出前定稿）

| 类别 | 题数 | 说明 |
| --- | --- | --- |
| 答复：单文档定位 | 8 | 答案在一份资料的一处或相邻几处 |
| 答复：跨文档 | 8 | 至少需要两份资料，例如收费依据加收费分支，或新旧两份资料对比 |
| 答复：多模态 | 4 | 关键断言必须依赖 XLSX 单元格或扫描 PDF 页 |
| 停止：范围或分支 | 4 | 越出河南本省户籍或本事项范围、把首次申领条件套到换领等 |
| 停止：证据不足 | 3 | 资料里没有写的内容，例如具体窗口、今天的金额 |
| 停止：版本、冲突或来源边界 | 3 | 要求判断哪份现行有效、把页面日期当施行日、异单位直接相减等 |

来源不够支撑某一类时，减少该类题数并写明原因，不编造题目凑数。

## 出题规则

1. 只依据上表原文出题，每道答复题写明关键断言和"来源 ID + 定位"（条款号、段落、单元格或 PDF 页码）。
2. 停止题写清应在哪个边界停止，不附可以拿来作答的业务事实。
3. 避开已用于开发的问题：李嘉杰筛查时至少对照 `evaluation/knowledge/` 下的草案与归档题、`data/knowledge/update-test-pairs/henan-id-time.json` 的测试问题，以及南京、河南演示情境。
4. 参考答案只保存在出题人自己的电脑上。GitHub 上只提交 `questions.json`（题号和问题）与 `freeze.json`（参考答案文件的 SHA-256、冻结时间、出题人身份、独立性声明）。两种方案都运行完后，再提交 `reference.json`。

`questions.json` 每题只含 `id`、`question` 和必要的情境说明（例如"按2022年规范回答"），不含答案线索。

## 运行前须由陈子炫补齐

- **问答入口已搭好**：见[自由提问入口与评测运行说明](../../docs/competition/自由提问入口与评测运行说明-2026-10-30.md)。两种方法共用上表 7 份资料构成的 581 个检索单元、同一检索和同一模型；"本方案"另加范围检查和确定性核验。正式运行前要冻结它的代码提交号。
- 冻结信息：代码提交号、本体版本、模型与参数、每题证据条数和调用上限、随机种子。
- 费用估算：先跑 8 题先导，再决定 30 题是否全量运行。
