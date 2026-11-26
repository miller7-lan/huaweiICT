# V4.0 r22 正文与技术附录

2026-11-26 在 r21（V3.1）Word 副本上逐处更新为 V4.0 / V4.0.1，[r21](../materials-r21/README.md) 原件不改。源稿：[V4.0 材料更新包](../materials-v40-20261126/README.md)。

**Word/PDF 不在本仓库。** 正文智能体展示图和附录 7.9 换用了 2026-11-26 的 V4.0.1 界面截图，截图上有答复原文，按公开仓库只放计数、编号和散列的规则，四份文件存私有仓库 `materials-r22/`。

| 文件 | SHA-256 |
| --- | --- |
| ICT-V40-r22正文.docx | `ac4e0047c0b172caba9004def04e235c0d7460c8711c94759a507bbb7efae3d3` |
| ICT-V40-r22正文.pdf（22 页） | `a3bd422e615f00f74612b6603330c913b773d00c675d75d5a7d50afd17741841` |
| ICT-V40-r22技术附录.docx | `dcbdf614d9dce03d762f99717f717ca6ce0226f184ebc5d44914bcb8df733381` |
| ICT-V40-r22技术附录.pdf（71 页） | `4cc379917fbc52caa3251b3f65a2dbce17f57ccb02b3c5b1850bce206b9b5fd6` |

**改了什么。**
- 正文：封面版本、摘要、智能体展示图与图注、阅读导航、状态说明、跨文档问答改进（V4.0 选型、Y12 开发对比、V4.0.1 原生）、复现、适用边界、代码说明、参考 [3]。
- 技术附录：稿件日期与版本、阅读导航、1.4、表 1-1、2.4（模型与发布状态）、表 7-1、7.7 提交件、7.8 说明、8.5，8.7 的 V3.1 改标为“已由 V4.0 接替”。
- 正文图 1-1 换为 V4.0 流程图（[源图](../assets/v4-figures/v4-flow.svg)，由 `scripts/draw_v4_flow.py` 生成）；正文与附录的 U-3 改为引用模板计时回执（[timing.json](../../../evidence/skill-template-timing-20261126/timing.json)）。
- 技术附录新增 7.9（V4.0.1 配置、调用关系、调试过程、图 7-17—7-19）和 8.8（V4.0 冻结、模型选型、Y12 开发对比）。

**核验。**
- PDF 由 Windows 本机 WPS（Word COM 接口）导出，与 r21 相同。
- 附录目录是静态文本，已按导出的 PDF 逐项校正页码，各条目与实际页码一致。
- 正文比 r21 多 1 页，末页只有 8 行。
- 全文不再有“当前 V3.1”“正式采用 V3.1”一类表述。

**尚待。**
- Y12 双人评审结果补入附录 8.8 和正文。
- 三人通读。

## 提交候选包

`ICT-V40-r22提交候选包-20261126.zip`，699 个文件，SHA-256 `07cb9f2b92b310de850185eae26852e4ca978a03f54eba34dd5124dd618ab88c`。

- 内容：V4.0 冻结源码（八个核心模块与冻结回执一致）、脚本、配置，以及 V4.0.1 智能体导出 `config/exports/frozen-flow-20261114/flow-agent.json`；另有 V4.0 与 V3.1 材料源稿，以及 101 份公开证据（两份源稿所链接的，加 r22 正文与附录中点名的）。
- 排除：`data/`、`evaluation/` 和内部待办。
- 核验：重新打开压缩包逐个比对散列，无不一致；在空目录解压后跑 12 个核心测试文件，Linux 与 Windows 11 上均 324 项通过（[Windows 自测](../../../evidence/material-review-20261126-r22/windows-selftest.json)）。队友任务清单等内部文档不入包。
- 记录：[package-verification.json](../../../evidence/material-review-20261126-r22/package-verification.json)。

r22 Word/PDF 不在包内（含截图，放私有仓库），上传时与包一起提交。压缩包本身也存私有仓库 `materials-r22/`。
