# V3.1 r21 材料与提交候选包

本轮以 r20 原件副本逐处替换版本、状态和附件说明，另存 r21，原件不覆盖。正文 21 页；附录页数以本轮核验回执为准。当前正式版本 V3.1，源码冻结 `459e459`、指纹检出 `b75409e`，历史实验保留原被测版本。

- [正文 Word](../../../artifacts/material-review-20261117-r21/public/ICT-V31-r21正文.docx)
- [正文 PDF](../../../artifacts/material-review-20261117-r21/public/ICT-V31-r21正文.pdf)
- [技术附录 Word](../../../artifacts/material-review-20261117-r21/public/ICT-V31-r21技术附录.docx)
- [技术附录 PDF](../../../artifacts/material-review-20261117-r21/public/ICT-V31-r21技术附录.pdf)
- [数字与证据对照](../materials-v31-20261117/数字与证据对照表.md)
- [本轮文件与渲染核验](../../../evidence/material-review-20261117-r21/verification.json)

本机 Word COM 接口实际由 WPS Office 提供，使用它导出 PDF，再经文档技能渲染入口及 bundled PDFium 生成页图核对，未使用未安装的 LibreOffice。旧界面图保留历史日期；当前三题使用负责人接收的方案 A，不伪造新截图。

V-1 同次完整中间轨迹、U-3 独立计时及精确被测提交仍缺；目录元数据未经逐项人工核对。原 30 题历史分歧与新 24 题合议分别保留。负责人已明确确认通读完成，M10 已接收；见[通读接收记录](../../../evidence/material-review-20261117-r21/readthrough-owner-confirmed-20261117.json)。四份 Word/PDF 和候选 ZIP 散列未变，原包及原核验中的 M10 待办是确认前快照，另存本次接收记录，不覆盖原件。赛事正式上传仍由负责人执行。

打包复用 `build_submission_manifest.py` 的选取、检查和压缩包复核功能；本地包装步骤额外排除 `data/`、`evaluation/` 中的资料和评测输入，以及内部回交待办，清单记录全部排除项。这些目录不属于本次默认对外提供的材料；对应私有资料不自动公开。源码、配置、Agent 导出及本轮四份文档入包，包内逐文件散列与包外 ZIP 散列均单列。

## r21b 包内核心自检

M11 使用更新后的打包脚本重新打包。`--exclude-dirs data evaluation` 排除默认资料与评测目录，仍保留脚本明确列出的合成对比台、公开扫描观察记录与公开来源目录，供核心测试读取；内部人工回交待办也排除。保留 r21 原包，不覆盖四份已通读的 Word/PDF。继续收录原包中的公开证据计数、散列和接收记录。

安装 `pyproject.toml` 声明的运行及 test 依赖后，在解压目录执行以下核心测试（PowerShell）：

```powershell
$env:PYTHONPATH = 'src'
python -m pytest -q tests/test_claim_binding.py tests/test_evaluation_tools_audit.py tests/test_regional_scope.py tests/test_frozen_qa_flow_v3.py tests/test_frozen_qa_flow_server.py tests/test_frozen_flow_native.py tests/test_flow_v3_review_pack.py tests/test_flow_v3_preflight.py tests/test_crossdoc_native_wiring.py tests/test_frozen_qa.py tests/test_frozen_retrieval.py tests/test_extract_fact_cards.py
```

只验证以上 12 个测试文件；完整测试依赖未随包公开的开发历史，不作为包内运行命令。自检属于合成/离线工程检查，不代表新增真实模型或业务验收。ZIP 散列及实际自检结果另记在包外 `evidence/material-review-20261117-r21/package-verification-r21b.json`；包外清单的 `zip_check.self_test.command` 保留核心测试命令。
