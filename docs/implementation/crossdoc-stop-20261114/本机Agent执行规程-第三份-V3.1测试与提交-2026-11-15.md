# 本机 Agent 执行规程（第三份）：V3.1 的测试、回放、决定与提交

读者：在本机执行 V3.1 测试、回放、原生运行、评审和回交提交的 Agent。

被测分支：`claude/blissful-knuth-5d407o`。被测提交以 `git log -1` 为准，不早于本文所在提交。

[第一份规程](本机Agent执行规程-2026-11-14.md)定通用规则：预检定名、方案一致、收表即校验、回报格式。[第二份](本机Agent执行规程-第二份-本轮任务-2026-11-15.md)定上一轮 T1—T6 的硬性要求。本文定 V3.1 这一轮每项任务的输入、命令、完成标准和交回内容。三份都要遵守，本文最具体，冲突时以本文为准。

V3.1 的内容见[重构方案比较与选择](重构方案比较与选择-2026-11-15.md)：分句绑定核验，每条结论下附原文整句，停止只显示固定说明，范围门 v2，评测工具 F16—F24 已修。`fedefb9` 是保底版本，任何时候都不改它。

## 本轮的时间与决定点

| 日期 | 任务 | 是否调用模型 |
| --- | --- | --- |
| 16—21 日 | V1 离线测试，V2 零调用回放 | 否 |
| **22 日** | **V3 决定：交回 V2 结果，由用户决定用 V3.1 还是交 `fedefb9` 保底包** | 否 |
| 23—26 日 | 用户决定采用后：V4 冻结与指纹，V5 原生重跑，V6 24 题重跑，V7 双人盲评 | V5、V6 需授权 |
| 27 日 | 改材料 | 否 |
| 28 日 | 提交 | 否 |

**V3 之前不调用任何模型。** 没有用户在 V3 给出的明确答复，不开始 V4 及之后的任务。

## 硬性要求

以下九条适用于每一项任务，没有例外：

1. **按编号顺序执行。** 每项达到完成标准后再开始下一项。
2. **先预检。** 会调用模型的任务（V5、V6）开始前，预检结果必须是 `development` 或 `formal`，并已保存到 `evidence/`。新版预检缺题目、参考或语料任何一项都会给出 `BLOCKED`，这是正常行为，不要绕过。
3. **一项一个新输出目录或新文件。** 名称带任务编号和日期，例如 `evidence/crossdoc-flow-v3/V2-24-v31-20261116.json`。已存在的就换名，旧文件保持原样。
4. **只跑一次。** 每个真实运行执行一次。调用失败照实写进报告，它也是结果的一部分。
5. **上限先于运行。** 每项的调用上限写在下面。运行前向用户报上限并得到确认；实际调用数超过上限，这一项就是未完成。
6. **冻结的就不改。** 本轮固定 `strict` 方案、593 单元语料（`.runtime/frozen-qa-v3-titles-20261115/corpus.json`）和本文指定的题。运行中发现代码问题，记下来，本轮照旧跑完。
7. **评分值只由评审本人填写和修改。** Agent 只负责发包、检查、退回和统计。
8. **未完成就写未完成。** 某条完成标准没达到，报告里写“V? 未完成：<哪一条>，原因：<原因>”。
9. **只提交清单里列出的文件。** 题目、答复原文、参考答案、评审表、`key.json`、语料、私有报告、日志、截图和密钥都留在本机，不进 Git。

## 本轮输入

开始前确认下面五样都在，缺一样就停下问用户：

| 输入 | 位置 | 怎么确认是同一份 |
| --- | --- | --- |
| 593 单元语料 | `.runtime/frozen-qa-v3-titles-20261115/corpus.json` | 文件 SHA-256 以 `d5b5458c44a6` 开头 |
| 24 题 `records.jsonl` 与参考 | 上一轮 T2 用过的私有文件 | 回放后 `records_sha256` 以 `1767b59d7faa` 开头，`expected_sha256` 以 `9aa497c2f4e7` 开头 |
| 30 题 `records.jsonl` 与参考 | `.runtime/frozen-qa-20261030/eval/20261105T003300Z/` 与出题目录 | 回放后 `records_sha256` 以 `357567be79bb` 开头，`expected_sha256` 以 `0133ea3a7d3c` 开头 |
| 旧版回放基线 | `evidence/crossdoc-flow-v3/T2-24-titles-20261115.json`、`T2-30-titles-20261115.json` | 已在仓库里 |
| 两位评审 | 用户指定，未参与出 24 题 | 仅 V7 需要 |

散列对不上，说明拿错了文件。停下问用户，不要换一份文件继续。

## V1 拉取与离线测试（不调用模型）

```powershell
$env:PYTHONUTF8 = "1"
git fetch origin; git switch claude/blissful-knuth-5d407o; git pull
git log -1 --oneline
.\.venv\Scripts\python.exe -m pytest -q tests/test_claim_binding.py tests/test_evaluation_tools_audit.py `
  tests/test_regional_scope.py tests/test_frozen_qa_flow_v3.py tests/test_frozen_qa_flow_server.py tests/test_frozen_flow_native.py `
  tests/test_flow_v3_review_pack.py tests/test_flow_v3_preflight.py tests/test_crossdoc_native_wiring.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_frozen_qa.py tests/test_frozen_retrieval.py `
  tests/test_frozen_qa_flow_v2_comparison.py tests/test_extract_fact_cards.py
cd evaluation/refactor-bench-20261115
..\..\.venv\Scripts\python.exe run_bench.py --candidate G > ..\..\.runtime\V1-bench-G.json
cd ..\..
```

**完成标准**（全部满足）：

- 第一条测试命令 `242 passed`，第二条 `84 passed`。数目不同时，把失败的测试名和第一行报错原样交回，然后停下。
- 对比台 G 的结果：开发集错误拦截 42/42，开发集正常结论直接答出 34/34，留出集错误拦截 9/12，留出集正常结论直接答出 12/12，范围门 23/23，结论附原文 53/53。查看方法：

  ```powershell
  .\.venv\Scripts\python.exe -c "import sys,json; sys.path.insert(0,'evaluation/refactor-bench-20261115'); import run_bench; s=run_bench.summarize(json.load(open('.runtime/V1-bench-G.json',encoding='utf-8'))); print({k:s[k] for k in ('bad_blocked_dev','good_direct_dev','holdout_bad_blocked','holdout_good_direct','gate','sourced')})"
  ```

- 地区范围核对（F1，陈 11-16 批准）：本机 593 单元语料必须只含 `config/region-profiles/henan-frozen.json` 里的七个来源，且单元散列在批准清单内：

  ```powershell
  .\.venv\Scripts\python.exe -c "import json,sys; sys.path.insert(0,'src'); from gov_mcp import regional_scope as r; c=json.load(open('.runtime/frozen-qa-v3-titles-20261115/corpus.json',encoding='utf-8')); print(r.check_corpus(c['units'], c['manifest']['corpus_sha256'], r.load_profile()))"
  ```

  输出 `{'profile': 'henan-frozen', 'sources': 7, 'units': 593}` 即通过。报错 `REGION_PROFILE_SOURCE_NOT_APPROVED` 或 `REGION_PROFILE_CORPUS_NOT_APPROVED` 时，把报错原样交回并停下，不要改清单去迁就语料。

**交回**：提交号、两行测试结果、对比台六个数字、地区范围核对的输出。

## V2 零调用回放（不调用模型）

用 V3.1 回放上一轮留存的草稿。语料与 T2 相同，回放结果与 T2 的旧版结果逐项比对。

```powershell
.\.venv\Scripts\python.exe scripts/export_flow_v3_diagnostics.py --records <24题 records.jsonl> `
  --corpus .runtime/frozen-qa-v3-titles-20261115/corpus.json --expected <24题参考> `
  --output evidence/crossdoc-flow-v3/V2-24-v31-<日期>.json
.\.venv\Scripts\python.exe scripts/export_flow_v3_diagnostics.py --records <30题 records.jsonl> `
  --corpus .runtime/frozen-qa-v3-titles-20261115/corpus.json --expected <30题参考> `
  --output evidence/crossdoc-flow-v3/V2-30-v31-<日期>.json
.\.venv\Scripts\python.exe scripts/compare_flow_replays.py evidence/crossdoc-flow-v3/T2-24-titles-20261115.json `
  evidence/crossdoc-flow-v3/V2-24-v31-<日期>.json --output evidence/crossdoc-flow-v3/V2-24-decision-<日期>.json
.\.venv\Scripts\python.exe scripts/compare_flow_replays.py evidence/crossdoc-flow-v3/T2-30-titles-20261115.json `
  evidence/crossdoc-flow-v3/V2-30-v31-<日期>.json --output evidence/crossdoc-flow-v3/V2-30-decision-<日期>.json
```

不加 `--with-values`。

`compare_flow_replays.py` 的退出码含义：

| 退出码 | 含义 | 做什么 |
| --- | --- | --- |
| 0 | 每个方法的应停止题停止数没有减少，应答复题的停止数没有增加 | 继续写 V2 报告 |
| 1 | 至少一个方法违反上面任一条 | 写 V2 报告，**停下**，进入 V3 |
| 2 | 两份文件不是同一批草稿、同一语料或同一参考 | 停下问用户，不要换文件重试 |

**完成标准**：

- 四份 JSON 都已生成，`evidence_units_missing_in_corpus` 全为 0；
- 两次比对的退出码都已记录，`failures` 原样抄进报告；
- 报告有一张表，表头写明“回放，不是新成绩”：每个方法一行，列出应停止题“旧停止 → 新停止 / 总数”、应答复题“旧停止 → 新停止”，以及 `should_stop_all_stopped`；
- 只要有任何一道应停止题在新版回放里给出答复或部分答复，就在报告里点名题号。

**交回**：四份 JSON、那张表、两个退出码。

## V3 决定（不调用模型，由用户决定）

把 V1、V2 的交回内容发给用户，然后**停下等待答复**。报告最后一行写明：

- 两次比对都是 0：“V2 满足采用条件，请决定是否采用 V3.1。”
- 任一次是 1：“V2 不满足采用条件：<failures>。按计划应交 `fedefb9` 保底包，请确认。”

用户答复“采用 V3.1”后才进入 V4。答复“交保底包”就到此为止，后续任务不做。没有答复就不要往下做。

## V4 冻结与指纹（不调用模型）

1. 记下当前提交号，作为本轮冻结提交。此后 `src/`、`scripts/` 不再改动；`git status --short -- src scripts` 必须为空。
2. 生成原机指纹，交给异机操作者：

   ```powershell
   .\.venv\Scripts\python.exe scripts/reproduction_fingerprint.py make `
     --corpus .runtime/frozen-qa-v3-titles-20261115/corpus.json `
     --questions evaluation/crossdoc-refactor-20261113/fresh-questions.json `
                 evaluation/crossdoc-refactor-20261113/retrieval-probes.json `
                 evaluation/independent-20261030/pilot/dev-pilot-questions.json `
     --records <24题 records.jsonl> `
     --output evidence/repro-origin-v31-<日期>.json
   ```

   新版指纹包含 `claim_binding.py` 和 `scope_gate_v2.py`，范围门使用 v2。以前在 `fedefb9` 上生成的指纹不能用来比对 V3.1。
3. 把提交号、这份指纹文件、语料和 `records.jsonl` 按[异机复现](异机复现-V3-2026-11-15.md)的方式私下交给异机操作者。异机按同样参数生成指纹，用 `reproduction_fingerprint.py compare` 比对，应输出 `"same": true`。

**完成标准**：冻结提交号已记下，工作区干净，原机指纹已生成。

**交回**：冻结提交号、指纹文件名。异机比对结果由异机操作者回交。

## V5 原生重跑（需额度，上限 3 次原生运行）

1. 停掉现有 8787 服务，用冻结提交、593 语料、`strict` 重启：

   ```powershell
   $env:PYTHONPATH = "src"
   .\.venv\Scripts\python.exe -m gov_mcp.frozen_qa_flow_server --corpus .runtime/frozen-qa-v3-titles-20261115/corpus.json
   ```

   重启后，旧版服务留下的未完成任务会被拒绝接续（`TASK_BELONGS_TO_ANOTHER_WORKFLOW_SOURCE_OR_OPTIONS`），这是预期行为。服务启动时按地区配置核对语料，核对不过就拒绝启动；`/health` 里的 `region_profile` 应为 `henan-frozen`。
2. 重新登记草稿助手，让 Nexent 使用新的展示规则（显示 `final.display`）。先不加 `--apply` 看计划，把计划交给用户确认，确认后再执行：

   ```powershell
   .\.venv\Scripts\python.exe scripts/frozen_flow_native.py provision
   .\.venv\Scripts\python.exe scripts/frozen_flow_native.py provision --apply
   ```

   草稿保持禁用、不发布。
3. 带 `--native` 预检，`label` 不是 `BLOCKED` 才继续。
4. 依次运行 N01、N08、N05：`scripts/frozen_flow_native.py run "<题面>"`。

**完成标准**（每道题全部满足）：

- 回执里 `passed`、`transport_completed`、`protocol_valid`、`workflow_verified` 都是 true，`failures` 为空，工具顺序以 `govkb_flow_open` 开头；N05 是范围停止，应只有 `govkb_flow_open`；
- 最终答复里每条结论下面都有“原文（来源 定位）：”一行，且与服务端任务文件里的 `final.display` 一致，没有多出事实。这一条由人工对照私有报告和任务文件确认；
- `model_starts` 的数值已写进报告，达到 9 或 10 时单独标出；
- 调试界面截图两张已保存：工具调用过程一张，最终答复一张。截图里不能出现令牌、账号或密钥。

截图拿不到时，V5 的状态写“未完成：缺截图”，然后继续 V6。

**交回**：三份 `evidence/` 回执（新版只含计数、散列和状态）、截图文件名清单，以及每道题一行：题号、结果类型、`model_starts`、工具调用次数。

## V6 24 题重跑（需额度，上限 120 次调用）

三种方法在同一时间、同一模型版本下重跑，比只重跑 V3.1 一组更公平，配对也更干净：

```powershell
.\.venv\Scripts\python.exe scripts/flow_v3_preflight.py --round <本轮声明.json> --questions <24题题目> `
  --reference <24题参考> --corpus .runtime/frozen-qa-v3-titles-20261115/corpus.json `
  --output evidence/crossdoc-flow-v3/V6-preflight-<日期>.json
.\.venv\Scripts\python.exe scripts/compare_frozen_flow_v3.py --questions <24题题目> `
  --corpus .runtime/frozen-qa-v3-titles-20261115/corpus.json --reference <24题参考> `
  --arms baseline single_shot flow_v3 --live --max-calls 120 `
  --output .runtime/crossdoc-flow-v3/V6-24-v31-<日期>
```

这 24 题读过 V3 代码的会话也见过，预检会给出 `development`，这一轮只能叫“开发对比”。用户想压低调用数时，可以只跑 `--arms flow_v3 --max-calls 72`，但报告的折扣里要加一条“基线与原方案沿用上一轮记录，与 V3.1 不是同一时间运行”。只重跑一组时，不要手工拼接新旧 `records.jsonl`：评审包生成器会拒绝重复记录，也不应绕过。这种情况先停下问用户怎么生成评审包。

**完成标准**：

- 预检文件已生成，`label` 不是 `BLOCKED`；
- `plan.json` 里 `source_sha256` 含 `claim_binding.py` 与 `scope_gate_v2.py`，与冻结提交一致；
- `summary.json` 的实际调用数不超过上限；
- 报告按方法列出程序标签计数（答复、带缺项、部分答复、停止）。应停止题凡是给出了答复或部分答复，都点名题号。

**交回**：预检文件、`summary.json` 的计数、上面的标签表。题面和答复原文不交。

## V7 24 题双人盲评（不调用模型）

1. 生成评审包：

   ```powershell
   .\.venv\Scripts\python.exe scripts/build_flow_v3_review_pack.py --records .runtime/crossdoc-flow-v3/V6-24-v31-<日期>/records.jsonl `
     --corpus .runtime/frozen-qa-v3-titles-20261115/corpus.json --reference <24题参考> `
     --output .runtime/crossdoc-flow-v3/V7-review-<日期> --reviewers reviewer1 reviewer2
   ```

   两个评审名必须不同。生成器同时写出 `pack.json`，登记评审名单和口径版本 `flow-v3-review-rules-2026-11-15`。
2. 发给每位评审 `review.md` 和本人的 `sheet-*.csv`。`key.json` 留在组织者手里。
3. 评审开始前，把 `review.md` 开头的六列说明发给两位评审，特别说明三点：remark 备注在参考答案“有误”时必须写原因，并随表私下交回；“原文摘录”一列只看以“原文摘录：”开头的条目，没有就填“无摘录”；V3.1 的答复里每条结论下附有“原文”一行，那是系统附上的原文，不是模型写的。
4. 每张表交回后立刻检查：

   ```powershell
   .\.venv\Scripts\python.exe scripts/score_flow_v3_review.py --pack .runtime/crossdoc-flow-v3/V7-review-<日期> `
     --sheets <这张表>.csv --check
   ```

   退出码 0 表示合规，1 表示有问题。有问题就把问题列表原样发还评审本人修改，再交、再查。退出码 2 或 3 说明评审包缺 `rows.json`，停下问用户。**不要加 `--legacy-pack`。**
5. 两张表都通过后统计：

   ```powershell
   .\.venv\Scripts\python.exe scripts/score_flow_v3_review.py --pack .runtime/crossdoc-flow-v3/V7-review-<日期> `
     --sheets <表1>.csv <表2>.csv --output evidence/crossdoc-flow-v3/V7-review-summary-<日期>.json
   ```

   脚本会拒绝同一文件、同一评审、或不在名单里的评审，这些都不能绕过。

**完成标准**：

- 两张表 `--check` 的退出码都是 0，且都是评审本人提交的版本；
- 统计 JSON 已生成，`sheets_passed_rule_check_unchanged` 为 true，`legacy_pack_rules_not_checked` 为 false，`reviewers_distinct` 为 true；
- 两位评审在“结论是否正确”上不一致的题号已列出，并已交两人合议。合议谈不拢的记“未达成一致”，保留两人原值。

**交回**：统计 JSON、合议结果的计数。

## 提交回仓库

每完成一项，就把这一项允许交回的文件提交一次。

**可以提交**：

- `evidence/crossdoc-flow-v3/V*-*.json`：回放、比对、预检、统计；
- `evidence/repro-origin-v31-*.json`：指纹；
- `evidence/frozen-flow-20261114/` 下新版原生回执：只含白名单字段；
- 本轮报告 `docs/implementation/crossdoc-stop-20261114/V3.1本机执行报告-<日期>.md`；
- 接续文档 `docs/2026-10-18对话决策与接续记录.md` 里追加的一节。

**不可提交**：`.runtime/` 下的一切，包括 `records.jsonl`、私有报告、任务文件、评审包、评分表和 SSE 日志；截图；题面、答复原文和参考答案；`--with-values` 导出的文件；密钥和账号。

提交前逐项检查：

```powershell
git status --short
git diff --cached --stat
.\.venv\Scripts\python.exe scripts/check_public_evidence.py <本次暂存的每个 evidence JSON>
```

`check_public_evidence.py` 遇到以下情况退出码为 1：题面、答复、结论、引文、原文、缺项、备注之类的字段里有非空文本，或任何字符串像密钥。它只打印字段路径，不打印内容。退出码为 1 时，把该文件撤出暂存区（`git restore --staged <文件>`），停下报告用户。全部退出码为 0 才提交。

`.runtime/`、截图和 `records.jsonl` 不应出现在 `git status` 的暂存列表里。出现了就撤出，并报告用户。

提交说明用一行英文概括这一项，例如 `docs: record V2 zero-call replay of V3.1`。正文写明“no model call”或实际调用次数。

推送：只推到 `claude/blissful-knuth-5d407o`，`git push origin claude/blissful-knuth-5d407o`。**推送前先得到用户同意**，用户没说“推送”就只做本地提交。不推送 `main`，不建 PR，不改历史，不强推。

## 报告

文件名：`docs/implementation/crossdoc-stop-20261114/V3.1本机执行报告-<日期>.md`。开头固定：

```
名称：<各运行的名称，取自各自的预检文件；V1—V4 写“离线检查，无模型调用”>
被测提交 / 提示词方案 / 语料单元散列：
折扣：<逐条；没有写“无”>
未完成：<逐项；没有写“无”>
调用：<每项实际次数 / 上限；失败次数；重试次数>
```

正文按 V1—V7 分节，每节第一行写“完成”或“未完成：<原因>”，然后是该项要求交回的内容。回放数字一律注明“回放，不是新成绩”。程序标签（答复、带缺项、部分答复、停止）照原样写。“答对”“准确率”只在 V7 人工评分之后、引用评分统计时使用。做了本文之外的事，单独一节写明做了什么、为什么。

## 必须停下来问用户的情况

- 本轮输入缺任何一样，或散列对不上；
- V1 测试数目不符；
- V2 比对退出码为 1 或 2；
- V3 还没有得到用户答复；
- 预检为 `BLOCKED`；
- 任何应停止的题给出了答复或部分答复；
- 某项的调用即将超过上限；
- 提交前检查发现题面、答复原文或密钥；
- 本文与其他文档说法不一致，或遇到本文没覆盖的情况。
