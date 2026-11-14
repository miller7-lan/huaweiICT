# 两位评审的可填写 PDF

对应 r2 的8题、16份匿名输出；评审材料识别码为 **f3cb4af4bdce**。这里只发布空白评分表和规则，不含题目、答案、证据原文或 A/B 方法密钥。

## 评审直接下载

- [评审一：可填写 PDF](review-reviewer1-fillable.pdf)
- [评审二：可填写 PDF](review-reviewer2-fillable.pdf)

GitHub 预览用于阅读。请点击文件页的下载按钮，保存到本机，再用支持交互表单的 PDF 阅读器打开。每份10页，N01—N08每题A/B均须填写，默认没有任何预选评分。中文字体已嵌入。

**开始前向组织者领取同批次私有 review.md 和参考答案。** 公开 PDF 不替代私有评审材料。两位评审应分别领取各自的材料目录，不能拿组织者完整目录，否则会看到 key.json。材料正文与两位评审的回表均不要提交这个公开仓库。

填写步骤和四项标准在 PDF 第1页；每项是单选，第10页有完成确认。停止输出前三项选“不适用”，无摘录选“无摘录”，再填写停止合理性；答复的停止合理性选“不适用”。原文优先于参考，参考存疑可标“待确认”。

评完前不讨论，不看另一位评审的表或密钥。保存为 reviewer1-filled.pdf / reviewer2-filled.pdf，关闭后重开，确认选择仍在，再私下交回组织者。**不要打印成 PDF 或截图，表单会被压平，无法自动导入。** 可以私下保留批注；已填写PDF及其批注不公开上传。

## 组织者：私有材料和 CSV 导入

材料来自 `bf3735c` 的 `build_flow_v3_review_pack.py`，使用 r2 原运行记录与其591单元语料，默认种子 `20261114-review`。两个公开 PDF 与同一份 review.md 的 A/B 标签对应，未对方法重新排列。

本机材料目录位于 `C:\CodexSSH\windows-dev\ICT-crossdoc-v3-r3-1a3aaf6\.runtime\crossdoc-flow-v3\`：

- `send-reviewer1-r2-20261114`：给评审一，不含密钥。
- `send-reviewer2-r2-20261114`：给评审二，不含密钥。
- `review-r2-20261114`：组织者保管，包含 key.json，不能整个发给评审。

PDF 只适用于这个已经生成的材料包。重新生成或换一批材料时，应生成对应新表；不能把旧PDF和新A/B密钥混用。

收回两份填写后的PDF后，在仓库根目录执行（需要 `pypdf`；本次验证使用7.1.0）：

```powershell
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe -m pip install pypdf==7.1.0

.\.venv\Scripts\python.exe scripts/import_flow_v3_review_pdf.py `
  --pdf .runtime/reviews/reviewer1-filled.pdf --reviewer reviewer1 `
  --pack .runtime/crossdoc-flow-v3/review-r2-20261114 `
  --output .runtime/reviews/sheet-reviewer1.csv

.\.venv\Scripts\python.exe scripts/import_flow_v3_review_pdf.py `
  --pdf .runtime/reviews/reviewer2-filled.pdf --reviewer reviewer2 `
  --pack .runtime/crossdoc-flow-v3/review-r2-20261114 `
  --output .runtime/reviews/sheet-reviewer2.csv

.\.venv\Scripts\python.exe scripts/score_flow_v3_review.py `
  --pack .runtime/crossdoc-flow-v3/review-r2-20261114 `
  --sheets .runtime/reviews/sheet-reviewer1.csv .runtime/reviews/sheet-reviewer2.csv `
  --output evidence/crossdoc-flow-v3/review-r2-20261114-summary.json
```

导入会核对评审身份、版本、review.md完整散列、全部必选项和停止输出的填表规则；缺项、表单被压平或材料包不符时拒绝生成结果。不会上传文件，也不读取 A/B 密钥。四项核心判断按现有CSV契约输出；额外的停止合理性与参考问题写入私有remark，不改变原评分工具的统计口径。

只有两人都独立交回后才能揭盲计数。分歧由两名评审合议，组织者不能代评分。只上传评分脚本生成的无正文统计JSON，不上传填写后的PDF、CSV、备注、材料正文、参考答案或密钥。

## 验证与字体

已检查两份PDF均为10页、96组单选与3项确认，默认留空；合成输入的“点选—保存—读取—16行CSV数据映射”已验证，空表和评审身份不符会被拒绝。中文页面已渲染检查。合成验证文件不作为真人评审结果发布。

中文字体采用 [Noto Sans SC](https://github.com/google/fonts/tree/main/ofl/notosanssc)，以SIL Open Font License嵌入；许可证见 [FONT-OFL.txt](FONT-OFL.txt)。PDF没有JavaScript、联网提交按钮或方法密钥。
