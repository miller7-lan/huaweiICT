# Nexent知识库动态咨询联调记录

2026-10-16。本记录检验不同实际问题是否到达只读证据工具，以及未发布、范围外和越权指令的停止行为。业务资料仍未正式发布，不据此计算30题业务准确率。

## 接入方式

另建`govkb_consultation_preview_citizen`和`govkb_consultation_preview_staff`两个咨询预览Agent，ID16/17；固定问题QA Agent 10/11保留。预览仅供独立运行端`127.0.0.1:5022`调用，不作为当前Nexent默认网页入口已经接入的证明。

运行端使用现有已核验镜像，另有容器`nexent-knowledge-runtime`、回调地址、日志和工作区。它只对上述两个名字注入`govkb_original_question`变量，值来自实际原生请求，保留引号、换行及整句内容。模型执行固定调用模板，直接读取变量；不依赖模型重新复制或提炼用户问题。其他Agent仍得到原来的参数结构。

仅绑定实际可用的`govkb_public_evidence_mcp`的两项工具。导入后按服务名和工具ID明确选择，再导出核对真实绑定，匹配后才发布预览配置。工具审计只记录参数指纹，验证整句到达服务端，不记录被拒个人数据的原始问题。

[运行端实际检查](../evidence/knowledge-update/consultation-runtime.json)10项通过，含已加载模块、独立端口与工作区、健康状态、Docker恢复策略，以及原/M1/M2五个相关模块的实际指纹。没有修改原/M1/M2的SDK文件或原Agent配置。Docker服务离线、电脑关闭或休眠期间仍不承诺运行。

## 实际模型检查

[最新逐项结果](../evidence/knowledge-update/native-consultation.json)6项全部通过：群众材料、群众费用、工作人员时限、工作人员材料关系、十五岁范围外及群众自称工作人员要求发布。逐项核对原生工具顺序、实际服务审计、完整问题指纹、可信角色、真实停止结果，以及原文/发布计数不变；6份私有轨迹指纹重新核对相符。

原始SSE和会话信息存放在私有`consultation-traces`；共享结果保留问题、实际返回、检查项及私有轨迹指纹。该轮是开发验证集，曾据此调整接入和提示，不能包装为独立业务验收集。没有正向正式政策答案、业务Recall或业务延迟承诺。

## 保留的失败与修正

首次新Agent导入复用了已删除模拟MCP的同名工具行，产生“MCP tool collection is not initialized”，还未调用模型工具。修正为按真实服务的可用工具ID绑定，并在导出核对失败时停止发布。

随后一轮模型把代码列表作为最终文字返回，没有实际工具调用。补充执行模板后，普通问题转发成功；但两轮中的越权测试仍把整句提炼成业务短句，完整问题指纹检查失败，虽均保持群众身份、停止且没有候选泄露，也不计作整轮通过。因此改用独立运行端绑定原始变量，保留前四次失败记录：

- [失败记录1](../evidence/knowledge-update/native-consultation-8d3ec7948d394deca58eca78f94c64b3.json)
- [失败记录2](../evidence/knowledge-update/native-consultation-32fadb089db543d79fb43d3637f86cd4.json)
- [完整问题转发未通过A](../evidence/knowledge-update/native-consultation-44e96390cac248e2a7c9cf93fd6e0342.json)
- [完整问题转发未通过B](../evidence/knowledge-update/native-consultation-c219cc5d55a04e1f85f72f4ce153f454.json)

## 复现

需要现有Nexent基础设施、已运行的M2基线、模型和私有测试账号。先准备独立运行端，再启动、核对并配置预览；不会自动接管默认网页路由。

```powershell
.\.venv\Scripts\python.exe scripts\prepare_knowledge_runtime.py
docker compose -f .runtime/knowledge-update/native-runtime/compose.json up -d --no-build --pull never knowledge-runtime
.\.venv\Scripts\python.exe scripts\verify_knowledge_runtime.py
.\.venv\Scripts\python.exe scripts\provision_knowledge_qa.py --consultation
.\.venv\Scripts\python.exe scripts\verify_knowledge_consultation.py
```

真实业务发布仍需解决本地材料关系、后续政策与当前发布条件；两端正式页面及交接验收另行完成。
