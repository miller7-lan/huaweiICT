# 南京事项完整公开项目快照

## 当前v2交付

- [下载v2 ZIP](nanjing-project-v2.zip)，20,486,791字节，1465文件。
- [v2同内容解压目录](nanjing-project-v2/)。
- [六页技术说明Word](../docs/南京真实事项技术说明.docx)，已渲染逐页检查。
- [v2完整性与新环境实跑](../evidence/real-matter-closure/project-delivery-v2/)。

v2源码提交 `20a87385efdadcecb57b3ad4f2d8e4152c0e9308`，包含Qwen原生v4成功实现、实际轨迹、旧失败记录、来源原件及Word。ZIP SHA256：`9d7a907826190121b60880fdeda1d5711f0b3c7cc61dc9c4df7f3486defa746e`。

已从GitHub提交 `03fed176383170e72ee78ec8b1f3e327c7c8a6fd` 实际下载v2 ZIP与独立Word，均为HTTP200/哈希一致，下载ZIP内的Word也与独立版一致。见 [下载回执](../evidence/real-matter-closure/project-delivery-v2/github-download.json)。

1464项文件清单与解压目录一致；新建Python环境从解压目录完成19项真实本地HTTP检查。8份相关原生源码核对实跑原始哈希后，再按CRLF/LF规范化比对包内字节一致；这次解压检查没有追加模型运行，也未重建Nexent容器。原生v4实际成功来自已记录的369.2秒单次活动，正式业务发布及独立评测仍未完成。

## 历史v1交付

- [下载ZIP](nanjing-project-v1.zip)，20,391,169字节。
- [同内容解压目录](nanjing-project-v1/)，1448文件。
- [复现说明](../docs/南京事项项目交付与复现.md)。
- [完整性及解压实跑回执](../evidence/real-matter-closure/project-delivery/)。

ZIP SHA256：`a3aebfe3091d5a0b0fb9255031077d060bc35b6be5d48a7fbcee580567c255c5`。
源码提交：`e1341095f5531809c7a02a147538b35438bde0f1`。快照内文档记录截至该提交的状态，解压后实跑及GitHub下载回执另存上述证据目录，不回写封存包。

全新Python环境、解压项目、新随机私有工程账户及真实本地HTTP双端19项通过；不调用模型、不刷新归档来源日期。v1封存时原生Qwen完整编排尚未通过，后续v4成功已收入v2；人工规则/OCR复核、正式发布、独立评测和新Nexent容器复现均未完成。旧包保持原样。
