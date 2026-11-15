# 地区级 Agent 隔离审计（报告分支）

审计最新main：`37e318ca12b33f453f99ab68962ab370a297d3ff`。日期2026-11-15。冻结V3代码基线：`fedefb92bb39576468f61e0b781f172b6d6239d2`；四个指定V3源码与冻结基线字节相同，见[来源记录](evidence/provenance.json)。

目标：坚持一个地区一个独立政务Agent；共享工具、模板、模型与通用算法，禁止地方政策未经适用确认跨区使用。不开发全国路由，不将多地区候选共存等同污染。

结论：**未取得现有真实知识库/线上结果已经跨地区污染的证据；发现并实跑复现了V3在误载混合语料时的越区引用缺口。** 演示/历史预览中的按情境隔离不等于所有Agent均已实现服务端地区身份绑定。

## 交付

- [架构与十项问题](architecture.md)
- [真实调用链与数据流](data-flow.md)
- [风险分级](risk-report.md)
- [测试矩阵、日志与限制](test-matrix.md)
- [待批准的最小修复](fix-plan.md)
- [比赛说明及本人时间](competition-summary.md)

## 范围与执行

只读分析源码、公开配置/导出/历史证据；新建本目录报告与审计测试。测试在Linux VPS隔离检出中进行；临时数据库的工程夹具审批/发布仅验证状态机，未接触用户Windows正式知识库、冻结语料、现存比赛证据、运行中Nexent或付费API。未修改核心代码、Agent JSON、部署或main。运行中的Nexent明确为**运行时未验证**，公开导出只代表历史静态配置。

既有定向测试296通过，补充绑定/发布检查77通过；新增11个审计用例通过。这里“通过”包括成功复现风险的诊断断言，**不是11项隔离全部安全**。原始日志、JUnit与模拟证据在[evidence](evidence/)。初次审计测试收集因错误导入失败，修正测试导入后执行；失败日志保留，业务代码未改。

复现：在该SHA检出、安装项目测试依赖后，`python -m pytest docs/competition/region-isolation-audit-202611/test_region_isolation.py -q`。该测试会把结构化观察写入本目录evidence/probe-results.json，所有源文均为明确标注的模拟内容，网络连接在审计用例中禁止。

本机连接备注：本轮尝试Windows只读文件工具建立审计输出目录，工具持续未返回而中止等待；因此未进入当前Windows服务health、Nexent或私有corpus检查。不能以VPS localhost代替Windows部署状态，也未读取令牌或账号。
