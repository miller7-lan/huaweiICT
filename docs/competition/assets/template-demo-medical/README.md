# 医疗行业模板渲染演示（2026-11-07）

用 `scripts/workflow_template.py` 和规格 [demo-medical-guideline.json](../../../../config/skill-templates/profiles/demo-medical-guideline.json) 渲染出的医疗诊疗规范归档预览 Skill 与主控协议，用来说明编排层、协议层可以按规格换行业生成。

- `demo-medical-guideline-archive/SKILL.md`：渲染出的 Skill，读 Skill、开任务、检索子智能体、核验子智能体、读状态、完成的顺序与政务链路相同，事项、证据说明和输出不变量换成医疗表述。
- `PROTOCOL.txt`：同一规格渲染出的主控协议。
- 规格校验通过，无 `TODO` 或占位符残留，不含政务事项字样；重复渲染判定为 `unchanged`。0 次网络与模型调用。

**只是渲染演示**：没有医疗来源适配器、候选构建或依赖计划算子，没有在 Nexent 中创建、导入或运行，也没有任何医疗数据；不构成医疗建议，不代表已完成跨行业迁移。真正落地仍需按[模板说明](../../Skill工作流模板说明-2026-10-30.md)第3—5步开发执行层并验收。
