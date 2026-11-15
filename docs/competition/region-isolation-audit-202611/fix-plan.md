# 最小修复建议（等待陈批准，尚未实施）

## 复现

运行[审计用例](test_region_isolation.py)的`test_08_wrong_corpus_exposes_missing_region_guard`：创建临时FlowService，输入河南23元与南京99元两个模拟unit，查询河南归档收费，提交把南京引文写成河南99元的草稿。final_checks为true，formal_approval仍false。[完整输入输出](evidence/probe-results.json)；机制见[frozen_qa_flow_server.py::open](https://github.com/miller7-lan/huaweiICT/blob/37e318ca12b33f453f99ab68962ab370a297d3ff/src/gov_mcp/frozen_qa_flow_server.py#L89)和[frozen_qa.py::verify](https://github.com/miller7-lan/huaweiICT/blob/37e318ca12b33f453f99ab68962ab370a297d3ff/src/gov_mcp/frozen_qa.py#L225)。普通用户不能通过这条接口上传unit；触发前提是服务端误配/错误构建的corpus。

R2另用“江苏户籍，在郑州办理身份证。”及仅引用外地标题的本地问题：都是OUTSIDE_HENAN_SCOPE；应保留外省户籍业务限制并解释户籍原因，不将停止改成强答。

## 建议分两小补丁，均需批准

### F1 初赛前优先：河南服务启动/证据范围约束

- 新建地区profile侧车配置，固定河南Agent身份、事项/分支、批准的corpus_sha256与七source_id及原件身份；national两源显式列举，不允许任意国家文件自动加入。
- 在服务启动以及检索池/最终assertions处核对批准来源；错误profile/未知来源拒绝服务，不通过自动删掉外地unit悄悄继续。对从Nexent索引取证的whole_corpus路径使用同一校验。
- 可能修改：新增`src/gov_mcp/regional_scope.py`（名称待定）、`config/region-profiles/henan-frozen.json`、`frozen_qa_flow_server.py`、`frozen_qa_server.py`及针对性测试。尽量不改检索排名、frozen_qa_flow_v3.advance/salvage和算法预算。
- 由批准profile校验既有unit与固定散列，不给旧unit追加字段，因此**无需改现有581/591/593语料字节或散列**；若以后扩充元数据，另建新语料版本。对外配置不得从用户question读取地区。

### F2 明显误停止/解释问题

- 仅在河南试点内区分办理所在地、明确户籍所在地与外地文件引用；外省户籍仍停止，原因明确为“河南本地户籍试点未覆盖外省户籍在郑州办理”。办理地在外地则另码停止；来源不足的归档比较按资料不足停止。
- 可能修改：`frozen_qa.py::scope_gate/GATE_MESSAGES`与新scope辅助模块；添加外省户籍、本地户籍外地引文、真实外地办理及不明确句式的回归。
- 不实现全国地名识别；不根据“江苏”一个词推断办理地；不扩大河南户籍/事项授权。

## 绑定处理（先只读确认）

- 为展示用各地区Agent登记“地区—Agent ID—MCP URL/服务名—profile—corpus/index—版本”表，核对实际读取配置。当前历史河南/南京共用8785服务应明确仅临时切换，不能宣传两者并行独立服务。
- 历史govregional共享双情境Agent不作为“每地区独立Agent”的已完成证据；如需展示独立北京入口，再批准创建固定北京草稿或从展示中排除共享入口。
- 如确要同时运行地区服务，最小修改限各地区独立runtime、token与端点/服务注册，复用同一MCP实现；Agent JSON仅修改对应固定工具绑定，禁止开发全国统一路由。此项先确认需求，不能自行改部署。

## 影响与回归

|对象|F1|F2|
|---|---|---|
|冻结fedefb9/已有比赛证据|保持原Git提交和文件，新分支新提交；旧成绩不改|同左|
|既有V3结果|同一批准corpus的排名/取舍应保持；补差异检查|scope_gate变化会改变部分停止结果；旧24/30题不得换算新成绩|
|语料SHA|侧车方案保持旧散列|不变|
|Agent JSON|F1通常不用改变；确需端点隔离另行确认|通常不变|
|部署|新服务代码需维护窗口重启，核对profile；审计阶段未操作|同样需新版本运行，不能就地覆盖冻结服务|

批准后只在独立修复分支实施，定向回归＋本矩阵复测；M08必须由“能复现风险”改为“在加载或引用阶段明确拒绝”，并保持national+河南合法组合通过。M10继续确认scope head无跨区变化。若代码或配置改变再形成新验收记录，不自动合并main、不启动付费运行。

## 当前状态

只提交报告与诊断测试。**等待批准F1/F2后才修改核心代码。** 预计Agent实现与验证合计约半天内的工程工作（以实际故障和环境为准），不要求队长投入同等时间。无P0生产证据时，不为P2问题扩张工作范围。
