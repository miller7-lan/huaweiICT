# 南京扫描公报OCR下游联调

2026-10-19，沿用队友范围内已有的 `knowledge_pdf.windows_ocr` 及 Windows 识别脚本，实际调用一次，无新识别实现、模型下载或云调用。原件为已归档四页PDF，SHA256为 `95e8decc4bb5aecb5324b7cea311659d26245998b0c6d24049577c8e267220bd`。调用前后两份上游实现哈希一致。此次为自动接口验证，未冒充队友提交或人工业务复核。

实际结果和输入契约位于 [OCR交接目录](../data/real-matter-closure/nanjing/ocr-handoff/)。四页均返回，几何1708×2400，行数分别62、71、71、56。新下游适配器核对原件、页序、语言和现有校验契约，保存原始识别及派生文本到独立候选库，修订为 `rev-27eb33f936b088f84d395c45`。复核记录、发布版本、发布头均为0，定时监控关闭。

使用Poppler渲染并由AI查看全部四页，印刷页码651—654。第1页包含修改决定尾部与重公布法律，第2、3页续法律，第4页法律尾部下方另起草案说明。完整页级识别必须保留这些文类边界，不能把整个PDF或第4页全部作为法条。当前保持页级候选，尚未建立人工认可的条款切分。

视觉抽查发现识别缺字和错字：第1页图像“记载的项目”被OCR识别为“记的项目”；第4页“人民警察”被识别为“人民誓察”。双栏目录等阅读顺序也不等同人工排版顺序。第2页的交回原证、六十日、延长三十日等锚点可观察，但没有宣称逐字识别正确率。未经模板修补，保留 `requires_visual_review:true`、无置信分数，日期元数据保持未知。

8项契约检查通过，覆盖四页保存、原件/请求绑定，以及错哈希、缺页、乱序、伪造置信/复核和证件号输入的拒绝；这些用例是明确的测试夹具。实际接口回执见 [interface-receipt.json](../evidence/real-matter-closure/ocr-handoff/interface-receipt.json)，AI视觉边界见 [visual-scope.json](../evidence/real-matter-closure/ocr-handoff/visual-scope.json)。

复现命令（需当前Windows OCR环境；拒绝覆盖已封存结果目录）：

```powershell
.\.venv\Scripts\python.exe scripts/verify_matter_ocr_handoff.py --runtime .runtime/real-matter-closure/new-ocr-handoff --run-existing-windows-interface
```

此阶段完成现有OCR接口返回到隔离候选入库的联调。人工逐字/条款复核、文类切分批准及OCR进入问答发布仍待完成；南京问答当前继续使用已绑定的HTML来源，规则保持候选。
