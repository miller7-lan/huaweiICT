# V3.1 r21 材料与提交候选包

本轮以 r20 原件副本逐处替换版本、状态和附件说明，另存 r21，原件不覆盖。正文 21 页；附录页数以本轮核验回执为准。当前正式版本 V3.1，源码冻结 `459e459`、指纹检出 `b75409e`，历史实验保留原被测版本。

- [正文 Word](../../../artifacts/material-review-20261117-r21/public/ICT-V31-r21正文.docx)
- [正文 PDF](../../../artifacts/material-review-20261117-r21/public/ICT-V31-r21正文.pdf)
- [技术附录 Word](../../../artifacts/material-review-20261117-r21/public/ICT-V31-r21技术附录.docx)
- [技术附录 PDF](../../../artifacts/material-review-20261117-r21/public/ICT-V31-r21技术附录.pdf)
- [数字与证据对照](../materials-v31-20261117/数字与证据对照表.md)
- [本轮文件与渲染核验](../../../evidence/material-review-20261117-r21/verification.json)

本机 Word COM 接口实际由 WPS Office 提供，使用它导出 PDF，再经文档技能渲染入口及 bundled PDFium 生成页图核对，未使用未安装的 LibreOffice。旧界面图保留历史日期；当前三题使用负责人接收的方案 A，不伪造新截图。

V-1 同次完整中间轨迹、U-3 独立计时及精确被测提交仍缺；目录元数据未经逐项人工核对。原 30 题历史分歧与新 24 题合议分别保留。三人通读 M10 尚未完成，生成的 ZIP 是待通读的提交候选包，不能自动当作已正式提交。

打包复用 `build_submission_manifest.py` 的选取、检查和压缩包复核功能；本地包装步骤额外排除 `data/`、`evaluation/` 中的资料和评测输入，以及内部回交待办，清单记录全部排除项。这些目录不属于本次默认对外提供的材料；对应私有资料不自动公开。源码、配置、Agent 导出及本轮四份文档入包，包内逐文件散列与包外 ZIP 散列均单列。
