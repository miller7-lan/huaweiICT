# 重构方案离线对比台（2026-11-15）

合成案例，零模型调用，不含真实题面、答复、参考或个人信息。比较结论见[重构方案比较与选择](../../docs/implementation/crossdoc-stop-20261114/重构方案比较与选择-2026-11-15.md)。

- `cases.py`：审计 F01–F15 的复现与对照、缺项标注、范围门问题
- `perturb.py`：从 10 个合成句自动生成忠实转述与单点扰动
- `holdout.py`：候选 A 写成之后才编写的留出案例
- `run_bench.py`：P0（fedefb9）、A、C、E、F 五种候选的统一评分，含两次运行一致性和既有测试
- `results-20261115.json`：本次结果

    python run_bench.py --all --baseline-src <fedefb9 检出>/src --out results.json

分数只用于比较设计，不是准确率，不得写成成绩。
