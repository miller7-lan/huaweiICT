# 污染风险与分级

## 结论边界

**未发现已被证实的真实在用跨地区污染或正式跨区发布。** 这表示本轮没有相应生产证据，不表示生产已经通过。当前Nexent、Windows私有corpus、索引实际成员和正式head均未读验。M08在真实函数+临时混合语料+人工构造模拟草稿条件下，已证明程序可能接受跨区引用；不是实际模型生成记录，不是政策真值，也不是正式业务批准。

|编号|等级|发现与前提|证据|初赛处理|
|---|---|---|---|---|
|R1|P1，误配置后的高影响路径|V3加载完整性≠授权地区；混入NJ模拟源可被HN检索并通过final_checks。需操作者加载混合/错corpus，普通提问不能自行修改服务器corpus|M08，[frozen_qa_flow_server.py::main](https://github.com/miller7-lan/huaweiICT/blob/37e318ca12b33f453f99ab68962ab370a297d3ff/src/gov_mcp/frozen_qa_flow_server.py#L197)、[frozen_retrieval.py::EvidenceIndex](https://github.com/miller7-lan/huaweiICT/blob/37e318ca12b33f453f99ab68962ab370a297d3ff/src/gov_mcp/frozen_retrieval.py#L79)、[frozen_qa.py::verify](https://github.com/miller7-lan/huaweiICT/blob/37e318ca12b33f453f99ab68962ab370a297d3ff/src/gov_mcp/frozen_qa.py#L225)|建议前置修复：固定地区profile绑定批准的corpus散列/source集合，检索及最终引用再核对。未证明七源本身已污染|
|R2|P1|关键词省市判断未区分办理地/户籍/资料标题；外省户籍示例停止可合理，但原因过泛；本地户籍问话仅引南京字样也一律停|M04/M06/M09，[frozen_qa.py::scope_gate](https://github.com/miller7-lan/huaweiICT/blob/37e318ca12b33f453f99ab68962ab370a297d3ff/src/gov_mcp/frozen_qa.py#L116)|最小补充分支化原因与有限上下文识别，保留本地户籍限制，不强制回答|
|R3|P1|旧BJ/NJ共享预览Agent组；HN/NJ固定链导出共享MCP命名/端口。按场景隔离≠每地区独立实例；当前映射和profile未核验|[导出清单](evidence/agent-export-inventory.json)，[provision_regional_native_preview.py::main](https://github.com/miller7-lan/huaweiICT/blob/37e318ca12b33f453f99ab68962ab370a297d3ff/scripts/provision_regional_native_preview.py#L92)、[provision_henan_multimodal_native.py::profile_provisioner](https://github.com/miller7-lan/huaweiICT/blob/37e318ca12b33f453f99ab68962ab370a297d3ff/scripts/provision_henan_multimodal_native.py#L55)|初赛前核对并限制每个展示入口的固定地区；并行使用才需要独立端点/令牌，不做全国路由|
|R4|P1|候选目录部分region unknown、matter unclassified/两种编码；V3 unit未保留统一地区/事项/分支/时间适用元数据。现有门未因此自动发布|[来源元数据](evidence/source-scope-inventory.json)，[build_frozen_corpus.py::unit](https://github.com/miller7-lan/huaweiICT/blob/37e318ca12b33f453f99ab68962ab370a297d3ff/scripts/build_frozen_corpus.py#L114)、[registry.py::review](https://github.com/miller7-lan/huaweiICT/blob/37e318ca12b33f453f99ab68962ab370a297d3ff/src/gov_mcp/knowledge/registry.py#L255)|明确来源profile侧车映射、保持待审，不为“统一”擅改原件或真实法律效力。新版本处理，旧散列不覆写|
|R5|P1接入条件|通用maintenance Engine/Store按root全局版本，不是地区问答过滤器；若多地区直接共接其search，会共享所有授权源|[engine.py::search](https://github.com/miller7-lan/huaweiICT/blob/37e318ca12b33f453f99ab68962ab370a297d3ff/src/gov_mcp/knowledge/engine.py#L390)、[store.py::publish](https://github.com/miller7-lan/huaweiICT/blob/37e318ca12b33f453f99ab68962ab370a297d3ff/src/gov_mcp/knowledge/store.py#L191)|先确认未给地区问答Agent误绑维护搜索；不把静态潜在路径说成已发生污染|

## P0

本轮**未确认生产P0**，也没有写正式业务数据。若当前绑定检查发现R1路径已进入真实答复、外地地方政策已被认作本地政策、或R5被用于跨地区正式发布/版本覆盖，应立即按用户P0定义处理并暂停相应入口。M08模拟越区答复是风险复现，已标出前提，不能用“所有测试通过”掩盖它。

## 误停止不等于污染

“江苏户籍，在郑州办理身份证。”实测在检索前返回OUTSIDE_HENAN_SCOPE，无task无引用。现有message是“问题涉及河南以外的地区，冻结资料只覆盖河南本省户籍事项”，**没有字面声称办理地点在江苏**；但服务没有提取“户籍=江苏、办理地=郑州”，无法提供精确分支原因。试点限定本地户籍时维持停止合理。仅出现“南京”标题/引文也会拦截，属于输入意图识别过粗；归档比较若需外地资料而本库没有，应说明本库资料不足，不应承诺支持完整跨区比较。

## P2

全国统一识别/路由、自动行政区划映射、多地区迁移平台、复杂规范效力推理、新地区管理界面均不进入本次修复。国家级规则正确沿用与共享工具本身不是污染。单地区内部语义完整性仍需原有人工与规则核验，不能靠一次地区过滤解决。
