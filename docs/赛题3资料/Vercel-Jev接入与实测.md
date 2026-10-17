# Vercel Jev 接入与实测

日期：2026-10-17。已接入独立分类适配器和真实测试入口。本机已有密钥，用户完成绑卡后发起 2 次模拟请求，均返回 HTTP 403，第二次确认原因是 `FREE_TIER_MODEL_RESTRICTED`。固定的 Jev 1.13 尚未调用成功，不能报告真实分类准确率。回归记录见 `evidence/jev-live/regression.xml`；离线通过不能代表真实中文分类效果。

## 填写已有密钥

在项目 `.runtime/jev/credentials.json` 中填写：

```json
{"AI_GATEWAY_API_KEY": "填写已有的 Vercel AI Gateway 密钥"}
```

该文件已在 Git 忽略范围内，目录限制为本机所有者和 SYSTEM 访问。不要将密钥发到聊天或放入前端代码。也可以在运行进程中设置 `AI_GATEWAY_API_KEY` 环境变量；环境变量优先。TypeSafe 官方直连密钥不等于 Vercel Gateway 密钥。

## 运行与查看

在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe scripts/test_jev_live.py --live
```

只使用固定的 9 份中文模拟资料，验证三维分类、原文支持判断、选项倒序稳定性和原文关联。[分类合并优化](Jev分类优化说明.md)后，全部成功时共 19 次 HTTP 请求（原为 28 次），单个执行实例最多 32 次；鉴权、额度、限流或响应错误后停止继续发送。重复运行会重新计数，不是跨进程的账户用量限制，因此不要无必要重复运行。

结果保存至 `evidence/jev-live/latest.json`，包括分类情况、待复核比例、选项顺序变化、原文保留检查、耗时、调用用量及网关返回的费用。`completed` 仅表示执行完成，质量需看各项指标。手写模拟样例的结果不能当作独立业务验收准确率。

程序在已知费用达到 0.10 美元后停止后续请求，这是响应后的保护，不是服务端硬预算。失败请求也可能计费，账单以 Vercel 为准。本轮不代为充值或绑卡。实测确认当前团队免费层不支持固定的 Jev 1.13；绑卡没有使这两次请求获得访问权限。Vercel 的[定价说明](https://vercel.com/docs/ai-gateway/pricing)区分绑卡与购买额度进入付费层；后者会结束每月免费额度，不自动执行。

本轮首个请求记录保存在 `evidence/jev-live/attempt-20261017-01.json`，第二个诊断请求见 `evidence/jev-live/latest.json`。两次均没有成功费用回执，不能将报告中的已知成功费用 0 解释为账单已确认零收费。错误正文与密钥均不进入结果，只记录固定的错误类别。当前应先由用户决定是否购买额度，或另行提供其他有权限的接入方式；不要重复发送相同请求。

## 接入范围

`src/gov_mcp/jev_gateway.py` 实现现有分类接口，仅向官方 Vercel 地址发送请求，固定 `typesafe-ai/jev-1.13.0`，检查实际返回模型，保留来源关联与机器候选状态。接口依据 [Vercel TypeSafe 兼容文档](https://vercel.com/docs/ai-gateway/sdks-and-apis/typesafe)。

资料分类脚本新增 `--provider vercel_jev_live`；资料准备脚本在已有 `--classify` 基础上可选 `--classification-provider vercel_jev_live`。默认行为不变，配置密钥本身不会触发模型调用。

本轮仅启用独立 Jev 分类测试和可选资料准备入口。工作台心跳与主 Agent 流程仍使用原有离线角色和明确标注的 Jev 回放；Qwen、其他模型及 Nexent 原生多模型调度尚未因此启用。正式业务知识仍需维护者确认后发布。
