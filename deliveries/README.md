# 项目快照与资料交接包

## 河南新旧文件测试与复核

2026-10-21仅替换本次新旧文件测试资料。[完整ZIP](henan-update-review-20261021.zip)及[同内容目录](henan-update-review-20261021/)共15文件，包含2024旧文与2026后继文的官方HTML、标注的PDF/TXT测试摘录、8项AI差异核查和7题待执行预期。两份官网本轮正常HTTP200且哈希未变；[执行回执](../evidence/knowledge-update/henan-handoff-20261021.json)记录10项资料/只读HTTP检查通过。真人签字、模型问答和目标系统启用切换未完成。[任务与运行说明](../docs/河南新旧文件任务清单与交接-20261021.md)。

交付提交`669b69c`的ZIP与独立清单已从GitHub实际下载，均为HTTP200且字节与本地一致；ZIP内15个文件也逐个匹配解压目录。[下载回执](../evidence/knowledge-update/henan-handoff-20261021-github.json)记录86,304字节及SHA256 `80ab44b136d5e235da23c7b96831cd2c459d01373499db8261525f2fbffa75e1`。

## 南京事项完整公开项目快照

## 原生配置增量包

[下载配置ZIP](nanjing-native-agent-assets-v1.zip)及[解压目录](nanjing-native-agent-assets-v1/)包含实际导出的三个禁用草稿配置和Skill，4文件、4757字节；[来源、校验及恢复边界](../docs/南京原生配置导出与恢复边界.md)。这是去除平台数字ID的恢复参考，不是原生导入API请求体；没有在新Nexent平台验证冷恢复，不覆盖下面已冻结的v2项目包。

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
