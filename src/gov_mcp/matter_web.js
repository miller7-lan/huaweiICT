"use strict";
const el=id=>document.getElementById(id);
let csrf="",role="",activeCase=null,topics={};
const statusLabels={SOURCE_GUIDANCE_PENDING_REVIEW:"来源说明 · 待复核",DRAFT_PROOF_READY:"跨文档开发推导",NO_SUPPORTED_MULTI_DOCUMENT_PROOF:"依据不足，停止完整推导",NEEDS_CLARIFICATION:"条件待澄清",OUT_OF_SCOPE:"超出主分支",NO_RELEASE:"尚无预览版本"};
const applicability={exception_context:"例外上下文，不累计为主分支要求",needs_validity_clarification:"旧证有效性未确认，不能据此认定可用",condition_not_met:"当前条件不满足，不能据此认定可用",fee_relief_requires_separate_review:"减免资格需要单独复核",source_service_period_outside_current_date:"已超出来源所述服务期，不能继续套用"};
const fieldLabels={adult:"是否成年人",self:"是否本人办理",civil_capacity:"民事行为能力",household:"户籍范围",reason:"办理原因",region:"地区",matter:"事项",old_card_valid:"旧证有效性"};
function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function message(text,error=true){el("message").textContent=text;el("message").className=error?"error":"success";}
function clearPrivate(){activeCase=null;el("result").replaceChildren(node("p","请登录后查看本账户咨询。","empty"));el("history").replaceChildren();el("share-case").value="";el("share-token").value="";el("accept-token").value="";el("confirm-note").value="";el("handoff-panel").hidden=true;el("confirm-panel").hidden=true;}
async function api(path,data){const options={credentials:"same-origin",cache:"no-store"};if(data!==undefined){options.method="POST";options.headers={"Content-Type":"application/json","X-CSRF-Token":csrf};options.body=JSON.stringify(data);}const r=await fetch(path,options);const v=await r.json();if(!r.ok){if(r.status===401){clearPrivate();el("workspace").hidden=true;el("login-panel").hidden=false;el("logout").hidden=true;csrf="";}throw new Error(v.message||"操作未完成。");}return v;}
function parseBool(id){const v=el(id).value;return v===""?null:v==="true";}
function facts(){return {region:"南京市",matter:"resident-id-expiry-renewal",adult:parseBool("adult"),self:parseBool("self"),civil_capacity:el("capacity").value||null,household:el("household").value||null,reason:el("reason").value||null,old_card_valid:parseBool("old-valid"),urgent_exam:el("urgent-exam").checked,fee_relief_requested:el("fee-relief").checked};}
function sourceView(c){const box=node("div",undefined,"citation");const url=new URL(c.url);if(url.protocol==="https:"){const a=node("a",c.title||"打开官方来源");a.href=url.href;a.target="_blank";a.rel="noopener noreferrer";box.append(a);}box.append(node("div",(c.locator||c.segment_id)+" · 公布："+(c.published_at||"未知")+" · 生效："+(c.effective_from||"未知")));const d=node("details");d.append(node("summary","查看原文与完整上下文"));d.append(node("div",c.full_context||c.quote,"context"));d.append(node("div","原件指纹 "+c.original_sha256,"muted"));box.append(d);return box;}
function render(value){el("result").replaceChildren();el("handoff-details").hidden=true;el("confirm-check").checked=false;el("result-state").textContent=statusLabels[value.status]||"交接记录";activeCase=value.case_id||null;
const meta=node("div",undefined,"result-meta");meta.append(node("div","知识预览版本："+(value.version||"未创建")));if(activeCase)meta.append(node("div","会话编号："+activeCase));if(value.topic_label)meta.append(node("div","主题："+value.topic_label));if(value.state)meta.append(node("div","交接状态："+({draft:"待交接",awaiting_staff:"待工作人员接收",staff_received:"工作人员已接收",staff_confirmed:"工程交接已确认"}[value.state]||value.state)));el("result").append(meta);
if(value.declared_conditions){
  const f=value.declared_conditions;
  const yesno=v=>v===true?"是":v===false?"否":"未确认";
  const conditions=node("div",undefined,"result-meta");
  conditions.append(node("strong","本次会话声明条件（未作实名资格核验）"));
  conditions.append(node("div","成年人："+yesno(f.adult)+" · 本人办理："+yesno(f.self)+" · 户籍："+({jiangsu:"江苏省内",outside_jiangsu:"江苏省外"}[f.household]||"未确认")));
  conditions.append(node("div","民事行为能力："+({full:"完全",limited:"限制",none:"无"}[f.civil_capacity]||"未确认")+" · 原因："+({expiry:"期满换领",loss:"遗失",damage:"损坏"}[f.reason]||"未确认")+" · 旧证仍有效："+yesno(f.old_card_valid)));
  el("result").append(conditions);
}

if(value.status==="NEEDS_CLARIFICATION"||value.status==="OUT_OF_SCOPE"){el("result").append(node("p",(value.status==="NEEDS_CLARIFICATION"?"请先明确：":"这些条件超出本轮主分支：")+(value.fields||[]).map(k=>fieldLabels[k]||k).join("、"),"warn"));}
for(const s of value.sections||[]){const card=node("article",undefined,"card");card.append(node("h3",s.title),node("div",s.source_statement,"quote"));if(applicability[s.applicability])card.append(node("div",applicability[s.applicability],"warn"));card.append(node("p",s.scope_note,"scope-note"));for(const c of s.citations)card.append(sourceView(c));el("result").append(card);}
if(value.proofs&&value.proofs.length){const card=node("article",undefined,"card");card.append(node("h3","旧证的使用与交回"),node("p","在旧证未超期、本人期满换领的条件下，等待办理期间可按南京来源记载使用旧证；领取新证时按法律条文交回原证。","quote"));card.append(node("p","该结论连接了南京来源的使用条件、办理与领取阶段顺序，以及法律的交回义务；仍为待复核开发推导。","scope-note"));const trace=node("details");trace.append(node("summary","查看逐跳推导（含流程前提）"));
const terms={old_card:"旧证",processing:"办理阶段",collection:"领证阶段",usable_during:"该阶段未超期可用",precedes:"先于（流程种子）",return_at:"该阶段须交回"};
const steps=node("ol");for(const step of value.proofs[0].proof.inputs){steps.append(node("li",step.triple.map(term=>terms[term]||term).join(" → ")));}
trace.append(steps,node("p","阶段顺序为明确登记的流程前提，不能冒充法律原文。","muted"));card.append(trace);
for(const c of value.proofs[0].evidence)card.append(sourceView(c));el("result").append(card);}
else if(value.status==="NO_SUPPORTED_MULTI_DOCUMENT_PROOF")el("result").append(node("p","当前版本无法形成完整跨文档证明。保留缺口，不补造结论。","warn"));
if(value.not_decided&&value.not_decided.length)el("result").append(node("p","仍未核定："+value.not_decided.join("、"),"warn"));
el("handoff-panel").hidden=!(role==="citizen"&&activeCase);el("confirm-panel").hidden=!(role==="staff"&&activeCase&&value.state==="staff_received");}
async function refreshState(){const data=await api("/api/state");topics=data.topics;el("head-version").textContent=(data.version||"未发布").slice(0,12);el("history").replaceChildren();for(const c of data.cases){const b=node("button",topics[c.topic]||"咨询记录","history-item");b.append(node("small",c.case_id.slice(0,18)+"… · 版本 "+c.version.slice(0,10)));b.addEventListener("click",()=>perform(async()=>{render(await api("/api/read",{case_id:c.case_id}));await refreshState();}));el("history").append(b);}if(!data.cases.length)el("history").append(node("p","暂无本账户会话。","muted"));
if(role==="maintainer"){el("source-controls").replaceChildren();for(const s of data.sources||[]){const d=node("div",s.source_id+" · "+(s.enabled?"启用":"暂停"),"source-control");const b=node("button",s.enabled?"暂停":"恢复","secondary");b.addEventListener("click",()=>perform(async()=>{await api("/api/source",{source_id:s.source_id,enabled:!s.enabled,reason:"维护页面明确调整来源状态"});await refreshState();message("来源状态已更新。",false);}));d.append(b);el("source-controls").append(d);}}
if(role==="maintainer"){
  el("version-controls").replaceChildren();
  for(const version of data.versions||[]){
    const label=version.ontology_stage==="seed"?"种子本体v1":"关系演化v2";
    const row=node("div",label+" · "+version.id.slice(0,12),"source-control");
    const button=node("button",version.id===data.version?"当前版本":"切换到此工程版本","secondary");
    button.disabled=version.id===data.version;
    button.addEventListener("click",()=>perform(async()=>{
      await api("/api/rollback",{target:version.id,expected_head:data.version});
      await refreshState();
      message("工程版本已切换，当前来源检查继续生效。",false);
    }));
    row.append(button);el("version-controls").append(row);
  }
}
const selected=el("topic").value;el("topic").replaceChildren();for(const [id,label]of Object.entries(topics)){const o=node("option",label);o.value=id;el("topic").append(o);}el("topic").value=selected||"materials";}
async function session(){const data=await api("/api/session");const local=data.authentication_mode==="local_engineering_accounts";el("auth-description").textContent=local?"使用本次独立工程账户登录。账户由维护者提供，不连接居民实名信息。":"使用已分配本事项角色的Nexent账户登录。";if(!data.logged_in){clearPrivate();el("login-panel").hidden=false;el("workspace").hidden=true;return;}
csrf=data.csrf;role=data.role;el("identity").textContent=data.role_label+(local?" · 工程账户":" · Nexent");el("workspace-title").textContent=data.role_label;el("login-panel").hidden=true;el("workspace").hidden=false;el("logout").hidden=false;el("query-panel").hidden=!(role==="citizen"||role==="staff");el("accept-panel").hidden=role!=="staff";el("maintenance-panel").hidden=role!=="maintainer";await refreshState();}
async function perform(action){message("");try{await action();}catch(e){el("result").replaceChildren(node("p",e.message,"warn"));el("result-state").textContent="操作已停止";el("handoff-panel").hidden=true;el("confirm-panel").hidden=true;el("share-token").value="";activeCase=null;message(e.message);if(csrf){try{await refreshState();}catch{}}}}
el("login-form").addEventListener("submit",e=>{e.preventDefault();perform(async()=>{await api("/api/login",{username:el("username").value,password:el("password").value});el("password").value="";await session();message("已登录。",false);});});
el("logout").addEventListener("click",()=>perform(async()=>{await api("/api/logout",{});csrf="";clearPrivate();el("identity").textContent="未登录";el("logout").hidden=true;await session();}));
el("query-form").addEventListener("submit",e=>{e.preventDefault();perform(async()=>{el("result").replaceChildren(node("p","正在核对原文与当前版本…","muted"));render(await api("/api/query",{facts:facts(),topic:el("topic").value}));await refreshState();});});
el("handoff-create").addEventListener("click",()=>perform(async()=>{const data=await api("/api/handoff",{case_id:activeCase});el("share-case").value=data.case_id;el("share-token").value=data.handoff_token;el("handoff-details").hidden=false;message("交接码已生成，仅交给指定工作人员。",false);await refreshState();}));
el("handoff-copy").addEventListener("click",()=>perform(async()=>{await navigator.clipboard.writeText(el("share-token").value);message("交接码已复制。",false);}));
el("accept-form").addEventListener("submit",e=>{e.preventDefault();perform(async()=>{const data=await api("/api/accept",{case_id:el("accept-case").value,token:el("accept-token").value});el("accept-token").value="";render(data);await refreshState();message("已接收，正在显示交接原版本。",false);});});
el("confirm-form").addEventListener("submit",e=>{e.preventDefault();perform(async()=>{render(await api("/api/confirm",{case_id:activeCase,acknowledge:el("confirm-check").checked,note:el("confirm-note").value}));await refreshState();message("工程交接已确认，未批准业务规则。",false);});});
session().catch(e=>message(e.message));
