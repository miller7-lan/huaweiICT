# 双线推进：第一阶段

当前阶段入口：[本次上传的全部结论](upload-summary.md)。本体14项修复已全部获本人判正确，旧失败保留；总账289次跨版本判断全部已回交。以下按各历史阶段保留原状态，最新验收与未完成边界以上传结论为准。

当前：[本体14项离线修复](ontology-normalization-results.md)已实现，12项定向检查通过；46条原模型候选中14条核心变化、32条不变，字段引用不匹配0。原候选与275次既有判断保留；新增14项复核分两组各7项，一份完整材料及空表可统一回交，新版人工评分0，暂不合并。

最新：[统一124项本人确认审核](unified-human-review-results.md)全部完成，71正确、38部分正确、15错误，注明开发者人工审核（可含模型辅助）。本体46条32/13/1，历史输出72份36/22/14；过滤和结构各1正确，四维度1正确3部分正确。原151次记录及失败保留，累计275次跨版本判断。未运行的独立新题、真实新对照、更新审批/回滚及Skill运行仍缺。

统一回交入口：[统一人工审核包](unified-human-review.md)。按本人最新要求已把46条恢复本体候选、72份历史输出、过滤1项、Skill结构1项及四维度表述4项合成124项，17组各不超过10项，一份完整材料和一张空白表可一次回交。原151次判断不重审；没有真实输出或具体候选的验收缺口单列，不代评分或批准正式变更。

当前入口：[所有人工审核总清单](human-review-inventory.md)，含[224条逐项记录/待审条目](human-review-all-items.csv)。本轮跨版本151次真实判断已记录；已备材料待审73项（72份跨文档历史输出及1项过滤决策）。另有历史46条模型本体候选和未准备的新验收任务，分开列明。V4.2最新3项均经本人判正确，旧版失败保留；独立与最终问答效果仍待验证。

当前：[第二批审核与V4.2修复](fact-cards-v4_2-results.md)。V4两批17项全部本人审核，12正确、5部分正确；其中两张后续V4.1已确认正确，历史失败保留。剩余B07、B08、B09已在V4.2补条件、年龄方向和领取动作；16项定向检查通过，同语料仅3张变化，新版3项待本人审核。下文为此前阶段记录。

最新回交：[V4.1两项本人审核](human-review-v4_1-summary.json)均正确，第13项主规则动作及对象范围、8周岁子规则层级获本人确认。V4原失败保留；下一批为[其余9项受影响候选](../../../.runtime/dual-track-improvement-202611/fact-cards-v4-r1/human-review-remaining-nine.md)，标记B01—B09，全部待审。以下为先前阶段记录。

最新：[V4首批审核与V4.1后续修复](fact-cards-v4_1-results.md)。本人首批8项为7正确、1部分正确；V4第二批9项待审。第13项主规则动作及对象范围已在独立V4.1修复，同语料仅2张关联卡片变化，12项定向检查通过，V4.1这2项仍待本人评分。V4已审版本与原记录保留。

当前任务：[V4剩余8项语义修复](fact-cards-v4-results.md)已完成实现与一次同输入离线对照，21项定向检查通过；2,137张卡片中17张语义字段变化，值/类型/原始定位不变，字段字符不匹配0。第一批8项和第二批9项材料均备好，新版人工评分0，暂不建议合并；原V3和全部已审记录保留。

当前结果：[新版V3开发者人工审核](human-review-v3-results.md)。62项候选已记录54正确、8部分正确（54/62，87.1%）；第14项过滤决策另列待审。本人确认第55、56项定位已修正，436处字符引用匹配所附原文；字符匹配与语义绑定分开评价。旧63评分、OCR四项评分及本轮冻结候选原样保留，无新API调用。

最新人工结果：[OCR四项本人审核](ocr-human-review-results.md)全部完成，2正确、2部分正确；两种方法各1正确、1部分正确，不外推总体准确率。期限保留“过去承诺40日（自然日）→30日”、起算范围保留“从受理之日起到领证之日”；第6页图的左6右3图面顺序获本人确认。页码/日期人工注记独立保存，原OCR与原63项卡片评分不改。

最新真实运行：[获批的4次OCR结果](ocr-four-pages-20261115-results.md)。两页×文字/富布局均返回HTTP200，总约14.8秒，零问答/零重试；第5页富布局文字漏掉落款日期，失败保留。按页价估算0.0009美元、实际扣费未知，本人已完成4项审核。下方“调用0”的离线交付记录是此前阶段快照，不代表当前累计调用仍为0。

最新交付：[本轮离线修复与真实验收边界](offline-finish-results.md)。对象/条件/日期/文号候选层、跨文档清单和版本保护、人工拒绝接口、多模态受控适配、居住证Skill复用代码均已完成本轮离线检查；169个去重定向用例最新结果通过。实际模型/上传/正式变更0，新版语义质量待本人及独立新题验收，不替代初赛版。

本人可查看[新版全63项对照](../../../.runtime/dual-track-improvement-202611/offline-finish-v3-r2/human-review-v3-all-63.md)和[4份OCR原图/输出对照](../../../.runtime/dual-track-improvement-202611/ocr-four-pages-20261115/human-review-ocr-four-items.md)。[4次OCR冻结计划](../../../.runtime/dual-track-improvement-202611/offline-finish-v3-r2/ocr-approval-plan.json)已获批并执行；[独立出题原件包](../../../.runtime/dual-track-improvement-202611/independent-authoring-packet.zip)尚未交给隔离出题环境。

入口：[真实基线](baseline.md)、[具体任务/预先标准/待批准预算](capability-plan.md)、[阶段结果与合并建议](final-summary.md)。

能力记录：[跨文档](crossdoc-results.md)、[多模态](multimodal-results.md)、[低资源本体](ontology-results.md)、[演化与Skill](evolution-skill-results.md)。比赛映射：[四维度证据](score-evidence.md)。

本轮检查38项通过、1条既有pydantic_settings警告；原始测试XML保存在隔离工作区`.runtime/dual-track-tests.xml`，散列和计数见[离线回执](offline-receipt.json)。只运行反馈与审核统计相关检查，没有全仓回归。新增模型调用0、上传0、正式状态变化0。没有业务正确率提升结论。

## 本人审核

- 事实卡片：63条同种子恢复样本，7批各9项。当前63项全部完成人工检查：15正确、45部分正确、3错误、0无法判断；见[完整审核结果](human-review-results.md)和[自动统计](human-review-summary.json)。原离线回执中的0完成是首次准备时快照，保留不覆盖。
- 本人已确认全部为人工检查，第34及54—63项采用最新补件复核，变更见[复核记录](human-review-rechecks.json)。原阅读包、建议和各次评分快照保留；后续转入实际缺陷修复，不再要求重复审核这63项。评分不构成更新批准。
- 24题历史匿名输出已从原评审ZIP按原顺序分8批，每批3题9份输出，位于隔离工作区`.runtime/dual-track-improvement-202611/crossdoc-24/`。只准备，不推定独立参考复核完成，不展示旧人工评分或方法密钥。每次只交一批给本人。
- [逐项评分账本](human-review.csv)与[样本指纹](review-manifest.json)仅含ID/类型/评分元数据，原文与候选全文留私有目录。只有本人实际选择后才填reviewer、decision、reviewed_at、note。队友后续先取得匿名阅读文件与空白表，不看本人结果。

## 审核材料补发

补发材料时，第19—63项先按“建议”单独保留；此后本人明确“其余也是人工检查”，45项已全部正式入账，其中11项采用补件后复核。此前18项统计作为历史快照保留。已补Excel结构、扫描原件与网页meta原文，见[缺口说明](review-material-gap.md)和[补充材料](../../../.runtime/dual-track-improvement-202611/source-supplement/README.md)。不自动改判或修改原样本。

## 离线复现

最新下一任务已完成三类局部修复：年龄/时长、经历序号与表格重复文本定位。V1及原评分保留，新V2候选19项定向检查通过，同一593单元离线对照已完成，见[V2结果与限制](fact-cards-v2-results.md)。新版本人工评分仍为0，待本人复核5个已知样本，不称独立效果提升。

在本隔离工作区运行，现有Python环境仅复用解释器及依赖，pytest从本工作区src加载源码：

```powershell
& 'C:/Users/18133/Desktop/ICT/.venv/Scripts/python.exe' -m pytest tests/test_dual_track_repair.py tests/test_dual_track_review.py tests/test_frozen_qa_flow_v3.py tests/test_frozen_qa_flow_server.py -q
& 'C:/Users/18133/Desktop/ICT/.venv/Scripts/python.exe' scripts/dual_track_review.py score
```

恢复原样本只在新隔离目录运行一次，指定593单元原语料；脚本核历史指纹，已有文件拒绝覆盖：

```powershell
python scripts/dual_track_review.py prepare --corpus <私有593单元corpus.json>
```

`scripts/audit_dual_track.py`从已完成测试XML写新回执，并以`--bundle`切分已有匿名ZIP；无模型调用。已有回执保留，复跑须给同目录下新的`--output`文件名，并省略已切分过的`--bundle`。

新独立留出题集当前**未建立**，不能把恢复的历史24题当新留出。后续在隔离出题环境生成，先冻结参考再获批运行，不根据留出结果反复调参。
