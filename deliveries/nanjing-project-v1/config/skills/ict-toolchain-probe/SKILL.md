---
name: ict-toolchain-probe
description: 用于 ICT 第三题本地工具链演练，先加载本技能，再调用已配置的政务 MCP 环境检查；不处理业务资料。
allowed-tools:
  - get_environment_status
tags:
  - ict
  - technical-probe
---

# 本地 Skill 与 MCP 联合演练

校验标记：ICT_TOPIC3_TECHNICAL_PROBE_V1。

1. 通过 read_skill_md 加载本技能并核对校验标记。
2. 只调用一次 get_environment_status，身份由 Nexent 会话注入。
3. 原样返回真实工具结果；EMPTY、BLOCKED 或 ERROR 都停止。
4. 不创建政策、材料、案例、本体、交接或知识版本。

此技能用于了解 Nexent 的上传、绑定、按需加载、MCP 调用和导出机制。
它不能作为政务资产认知、业务推理或动态本体的完成证据。
