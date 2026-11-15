# 知识与MCP数据流

## 河南V3

`candidate-catalog → build_frozen_corpus.SOURCES(七源固定选择) → 原件散列/条款检查 → corpus.json → --corpus → FlowService/EvidenceIndex → scope_gate(question) → retrieve → advance/verify → final(PAUSED_UNREVIEWED)`。

依据：[build_frozen_corpus.py::original](https://github.com/miller7-lan/huaweiICT/blob/37e318ca12b33f453f99ab68962ab370a297d3ff/scripts/build_frozen_corpus.py#L83)、[build_frozen_corpus.py::unit](https://github.com/miller7-lan/huaweiICT/blob/37e318ca12b33f453f99ab68962ab370a297d3ff/scripts/build_frozen_corpus.py#L114)、[frozen_qa_flow_server.py::open](https://github.com/miller7-lan/huaweiICT/blob/37e318ca12b33f453f99ab68962ab370a297d3ff/src/gov_mcp/frozen_qa_flow_server.py#L89)、[frozen_qa_flow_v3.py::advance](https://github.com/miller7-lan/huaweiICT/blob/37e318ca12b33f453f99ab68962ab370a297d3ff/src/gov_mcp/frozen_qa_flow_v3.py#L313)。脚本选择七源，不从多地区候选库直接全量加载。缺口在加载和检索/引用之间：输入corpus只校验自报散列，EvidenceIndex/verify不做地区allowlist复核。M08的输入只存在临时目录，未更换任何真实corpus。

39号旁路：`独立Nexent索引→knowledge_base_search→govkb_frozen_verify_corpus→final`。历史导出index_names只有一个；全语料核验仍只能证明引用来自已加载unit，不能独立证明地区适用。[provision_frozen_kb_native.py::knowledge_index](https://github.com/miller7-lan/huaweiICT/blob/37e318ca12b33f453f99ab68962ab370a297d3ff/scripts/provision_frozen_kb_native.py#L42)；[bind_frozen_knowledge_files.py::bind](https://github.com/miller7-lan/huaweiICT/blob/37e318ca12b33f453f99ab68962ab370a297d3ff/scripts/bind_frozen_knowledge_files.py#L26)；[frozen_qa_server.py::verify](https://github.com/miller7-lan/huaweiICT/blob/37e318ca12b33f453f99ab68962ab370a297d3ff/src/gov_mcp/frozen_qa_server.py#L57)。

## 多地区候选与正式registry

`采集→共享KnowledgeStore.registry(未审原件/metadata)→区域TARGETS/region+matter过滤→候选预览`。每条引用重新检查来源、URL、metadata、版本最新状态及原文。[knowledge_landing.py::retrieve](https://github.com/miller7-lan/huaweiICT/blob/37e318ca12b33f453f99ab68962ab370a297d3ff/src/gov_mcp/knowledge_landing.py#L56)。因此共库存储不是污染；M09含外地政策诱导的query不能改变server_region过滤结果。

`review(revision,人工确认)→candidate(region,matter)→publication质量门→publish→heads[region,matter]→KnowledgeTools固定scope→search(published version)`。[registry.py::review](https://github.com/miller7-lan/huaweiICT/blob/37e318ca12b33f453f99ab68962ab370a297d3ff/src/gov_mcp/knowledge/registry.py#L255)、[registry.py::candidate](https://github.com/miller7-lan/huaweiICT/blob/37e318ca12b33f453f99ab68962ab370a297d3ff/src/gov_mcp/knowledge/registry.py#L365)、[registry.py::publish](https://github.com/miller7-lan/huaweiICT/blob/37e318ca12b33f453f99ab68962ab370a297d3ff/src/gov_mcp/knowledge/registry.py#L382)、[knowledge_tools.py::execute](https://github.com/miller7-lan/huaweiICT/blob/37e318ca12b33f453f99ab68962ab370a297d3ff/src/gov_mcp/knowledge_tools.py#L22)。未知范围不能自动审核；不同地区head分开。时间窗口在search中执行，不能把发布日期当施行日。

## 本体、审批与更新

`来源包→build_candidate(身份/地区/事项/内容形状)→独立workspace候选→human structural review→active_rules→固定算子/依赖计划→返回前重查source与head`。北京、南京、河南profile不能随意互换。[ontology_evolution.py::initialize](https://github.com/miller7-lan/huaweiICT/blob/37e318ca12b33f453f99ab68962ab370a297d3ff/src/gov_mcp/ontology_evolution.py#L76)、[ontology_evolution.py::review](https://github.com/miller7-lan/huaweiICT/blob/37e318ca12b33f453f99ab68962ab370a297d3ff/src/gov_mcp/ontology_evolution.py#L179)、[ontology_crossdoc.py::query](https://github.com/miller7-lan/huaweiICT/blob/37e318ca12b33f453f99ab68962ab370a297d3ff/src/gov_mcp/ontology_crossdoc.py#L126)、[henan_multimodal.py::_documents](https://github.com/miller7-lan/huaweiICT/blob/37e318ca12b33f453f99ab68962ab370a297d3ff/src/gov_mcp/henan_multimodal.py#L43)。结构确认不等于业务审批。

河南successor链固定old/new source_id、时间单位和限定条件，只生成pending候选，不发布规则；[successor_update.py::validate_difference](https://github.com/miller7-lan/huaweiICT/blob/37e318ca12b33f453f99ab68962ab370a297d3ff/src/gov_mcp/successor_update.py#L86)。

需区别同名Store：`knowledge.registry.KnowledgeStore`按scope管理知识head；`knowledge.store.Store`是维护/本体的通用容器，active_rules按root隔离，不自带region键。维护Engine会读取配置中所有已授权源，非地区正式问答接口，不能无条件复用为地区Agent检索。[store.py::publish](https://github.com/miller7-lan/huaweiICT/blob/37e318ca12b33f453f99ab68962ab370a297d3ff/src/gov_mcp/knowledge/store.py#L191)、[engine.py::search](https://github.com/miller7-lan/huaweiICT/blob/37e318ca12b33f453f99ab68962ab370a297d3ff/src/gov_mcp/knowledge/engine.py#L390)、[server.py::create_app](https://github.com/miller7-lan/huaweiICT/blob/37e318ca12b33f453f99ab68962ab370a297d3ff/src/gov_mcp/knowledge/server.py#L31)。

## 特别绑定风险

历史govregional_controller按scenario依次运行北京和南京，这是有限双情境预览，不是独立两套地区Agent。河南三模态和南京固定链则是不同Agent组，但共享MCP服务名/端口、按runtime选择profile；调用错误场景有硬拒绝，尚未证明会静默混用政策。应核对实际端点/令牌/profile，不能仅看Agent名称。当前真实服务及KB没有连接检查，结论均为运行时未验证。
