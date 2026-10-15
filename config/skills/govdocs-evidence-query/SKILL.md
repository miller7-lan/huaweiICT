---
name: govdocs-evidence-query
description: 查询演练资料中的原文依据，保留来源和资料版本，并打开原件定位。适用于资料查询，不用于正式审批或缺少依据的业务推断。
allowed-tools:
  - govdocs_search_evidence
tags:
  - government
  - evidence
  - synthetic-rehearsal
---

# 资料查询与原文核对

校验标记：GOVDOCS_EVIDENCE_QUERY_V1。

1. 通过 read_skill_md 加载本技能。
2. 将用户本次问题原样传入 govdocs_search_evidence 的 query 参数，不使用固定示例替换问题；身份由 Nexent 注入。
3. 只执行一次查询，保留真实返回的片段、来源链接、资料版本及模拟资料提示。
4. 用真实结果 data.answer_markdown 作为最终回答。它是工具生成的原文摘录，不是模型自行撰写的办理结论。
5. EMPTY 表示当前未找到足够依据，停止并显示资料不足。BLOCKED 或 ERROR 同样停止，保留原因，不将故障写成业务不符合。

当前只查询已准备好的三类模拟资料快照。词面线索筛选加向量排序是基础检索，不能保证所有无依据问题都可被自动识别，也不构成本体或多跳推理。真实资料接入需要另行完成来源复核和原文访问权限。
