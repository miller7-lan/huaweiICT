# 反污染测试与实际结果

受测提交：`37e318ca12b33f453f99ab68962ab370a297d3ff`。全部为离线工程测试，模拟资料/草稿及Mock核验器；不把它们冒充当前Nexent原生结果或政策验收。原始记录：[probes](evidence/probe-results.json)、[测试源码](test_region_isolation.py)。

|编号|要求|已执行结果|证据与未验证边界|
|---|---|---|---|
|M01|河南本地正常检索|仅HN-AUDIT-FIXTURE，程序检查通过、formal=false|真实FlowService函数＋模拟河南unit；真实593单元检索待执行|
|M02|南京本地正常检索/计划|derived，证据仅NJ-ID-RENEWAL-CYCLE-2026、NJ-ID-OLD-CARD-2025|固定本体fixture source；真实当前部署待执行|
|M03|北京正常/错地区|北京source_conditions_result=true；河南参数OUT_OF_SCOPE；来源BJ-ID-RENEWAL-2025|本体fixture，非真人资格判定|
|M04|HN采用南京地方政策|OUTSIDE_HENAN_SCOPE，在检索前停止、不建task|真实gate；不是全面地区理解证明|
|M05|南京采用河南|out_of_scope，REGION_MATTER_OR_HOUSEHOLD_OUT_OF_SCOPE，无trace|固定计划实跑，不经过Nexent|
|M06|江苏户籍、郑州办理|停止；只给泛化范围原因；无引用|本地户籍限制下停止本身不算污染；原因角色不够明确|
|M07|national＋河南|HN三模态derived_archive；来源HN-FEE-CATALOG-XLSX-2025、MOF-FEE-2007、HN-MEASURES-2021-PDF；formal=false|测试来源类型与绑定，不代表现行收费|
|M08|同类事项不同收费/误载异地源|模拟河南23元、南京99元共载；HN问题检索到两者；构造“河南99元”引用NJ源，final_checks.passed=true|**风险已复现**。诊断测试通过表示确认缺口，非隔离通过；无真实模型调用|
|M09|标题、引用和query诱导|河南gate遇外地字样全部停止；候选库在北京/南京server_region分别仅返回自己的source_id|query不是trusted region；同库多地区并未导致候选检索混用；语义Prompt注入无模型端到端测试|
|M10|河南更新不改NJ/BJhead|临时registry河南head变化，南京、北京head字节标识均保持；before/after原值见JSON|engineering_fixture的临时审批/发布，未触碰真实正式head。真实三地版本比对待执行|

## 本次实际执行

1. 复用10个相关既有测试文件：**296 passed**，9.05秒；[log](evidence/existing-tests.log)、[JUnit](evidence/existing-tests.xml)、[命令](evidence/existing-test-run.json)。覆盖候选范围/伪造引用、地区本体、更新审批/回退、DecisionService地区与时间、V3状态机。
2. 本目录审计用例：**11 passed**，1.27秒；[log](evidence/probe-tests.log)、[JUnit](evidence/probe-tests.xml)。网络socket.connect在用例中禁止，V3草稿人工构造。M08是暴露风险的成功复现断言。
3. 增补既有scope、发布、MCP及HN/NJ provisioning检查：**77 passed**，2.40秒；[log](evidence/binding-tests.log)、[JUnit](evidence/binding-tests.xml)。命令：四个test_knowledge_update定向用例（省级范围、跨区拒绝、待审变更、待发布变更）＋test_knowledge_mcp.py、test_henan_multimodal_provision.py、test_ontology_crossdoc_provision.py。

既有告警为Starlette TestClient弃用及pydantic_settings前向引用；未为此升级依赖。首次新增测试收集失败为SCENARIO_FACTS导入位置错误；只修测试导入后执行，日志保留[evidence/probe-collection-attempt1.log](evidence/probe-collection-attempt1.log)，没有核心修复或隐藏重跑。

## 待执行（不能填通过）

- 当前Nexent各Agent的工具端点、Skill、禁用/发布状态、KB index_names和索引成员readback。
- Windows当前启动corpus完整散列与批准source集合核对；本轮只核对源码与历史公开清单。
- 当前河南/南京/北京正式head的前后只读比对；真实更新不得为了审计试写。
- 真实模型在不同地区政策诱导下的最终答复；本轮禁止消耗API额度，全部未执行。

测试不要求开发全国识别器；固定两地/三地fixture足够定位现有边界。当前结果不能写成“线上三地区隔离已实证全部通过”。

本机连接备注：本轮尝试Windows只读文件工具建立审计输出目录，工具持续未返回而中止等待；因此未进入当前Windows服务health、Nexent或私有corpus检查。不能以VPS localhost代替Windows部署状态，也未读取令牌或账号。
