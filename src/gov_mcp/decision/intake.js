'use strict';
const byId = id => document.getElementById(id);
const messages = {
  FORM_NOT_STARTED: '本会话还没有草稿，请先进入模拟填表。',
  FORM_NOT_OWNED: '草稿不属于当前会话，或已被另一个标签页替换。',
  FORM_EXPIRED: '草稿已过期并清除，请重新开始。',
  FORM_REVISION_CHANGED: '回答已在其他操作中更新，请恢复最新草稿后重试。',
  FORM_ANSWER_NEEDS_CLARIFICATION: '暂时无法可靠对应到选项。请重新描述、选择预备选项，或明确选“不清楚”。不会把这段文字交给模型猜测。',
  FORM_INVALID_ANSWER: '请填写 1–120 个普通字符，不填写证件号、联系方式等个人信息。',
  FORM_AI_NOT_ACTIVATED: 'DeepSeek 推理尚未启用；当前仅完成本地逐项填写，不能伪装成 AI 已核查，也不能跳过核查提交。',
  FORM_AI_UNAVAILABLE: 'DeepSeek 暂不可用，草稿保留；不会改用其他模型或自动提交。',
  FORM_AI_ROUTE_NOT_ALLOWED: '填表核查只允许已配置的 DeepSeek 官方端点，不自动转给网关或其他服务。请由本机维护人员核对配置。',
  FORM_AI_CHANGED_FACTS: '模型试图改变事实或标准值，结果已拒绝。请检查原回答后重试。',
  FORM_AI_INVALID_OUTPUT: '模型核查格式不合约定，结果已拒绝，没有提交。',
  FORM_AI_BUDGET_EXHAUSTED: '本会话或服务的核查调用预算已用完，请由本机维护人员处理，不会自动追加费用。',
  FORM_AI_BUSY: '核查服务繁忙，请稍后再试。',
  FORM_VERSION_CHANGED: '知识版本已变化。请重新核查并再次人工确认，旧确认不能提交新版本。',
  FORM_SCHEMA_CHANGED: '字段定义已变化，请重新填写，不能沿用旧格式提交。',
  FORM_HUMAN_CONFIRMATION_REQUIRED: '请先逐项核对并勾选人工确认。',
  FORM_EGRESS_CONSENT_REQUIRED: '请先检查选项代码，并明确同意仅将代码交给 DeepSeek。',
  FORM_SUPPORTED_BRANCH_REQUIRED: '当前没有已确认适用的咨询分支，请修正情况或等待资料补齐，不套用其他分支。',
  LOGIN_REQUIRED: '会话已退出或过期，请重新进入。'
};
let draft = null;
let editing = null;
let busy = false;
function element(tag, text) {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = text;
  return item;
}
function notify(message, failed = false) {
  byId('notice').textContent = message;
  byId('notice').className = failed ? 'error' : '';
}
async function request(path, value) {
  const response = await fetch(path, {method: value === undefined ? 'GET' : 'POST',
    headers: value === undefined ? {} : {'Content-Type': 'application/json'},
    body: value === undefined ? undefined : JSON.stringify(value)});
  const output = await response.json();
  if (!response.ok) throw Error(messages[output.error] || '操作未通过校验，请检查权限、回答及当前版本。');
  return output;
}
async function action(callback) {
  if (busy) return;
  busy = true;
  document.querySelectorAll('button').forEach(button => { button.disabled = true; });
  try { await callback(); } catch (error) {
    byId('ack').checked = false;
    if (draft) {
      try { draft = await request('/api/forms/state'); } catch { draft = null; editing = null; }
    }
    notify(error.message, true);
  }
  finally { busy = false; render(); }
}
function button(text, callback) {
  const node = element('button', text);
  node.addEventListener('click', () => action(callback));
  return node;
}
function body(extra = {}) { return {form_id: draft.id, revision: draft.revision, ...extra}; }
async function answer(field, text) {
  draft = await request('/api/forms/answer', body({field_id: field.id, answer: text}));
  editing = null;
  byId('customAnswer').value = '';
  byId('ack').checked = false;
  byId('consent').checked = false;
  notify('本项已保存；尚未向模型发送原回答。');
}
function rows(host, fields, canEdit) {
  host.replaceChildren();
  for (const field of fields) {
    const row = element('div');
    row.className = 'answer-row';
    row.append(element('strong', field.label), element('p', '原回答：' + (field.original ?? '已清除')),
      element('p', '规范值：' + field.display), element('small', '选项代码：' + JSON.stringify(field.value)));
    if (canEdit) row.append(button('修改本项', async () => { editing = draft.fields.find(item => item.id === field.id); byId('ack').checked = false; }));
    host.append(row);
  }
}
function render() {
  document.querySelectorAll('button').forEach(node => { node.disabled = busy; });
  byId('welcome').hidden = Boolean(draft);
  byId('workspace').hidden = !draft;
  if (!draft) {
    byId('answers').replaceChildren(); byId('reviewTable').replaceChildren(); byId('resultBody').replaceChildren();
    byId('customAnswer').value = ''; byId('ack').checked = false; byId('consent').checked = false;
    return;
  }
  byId('progress').textContent = `${draft.title} · ${draft.completed}/${draft.total} 项 · 草稿版本 ${draft.revision}`;
  byId('entryColumns').hidden = Boolean(draft.receipt);
  const current = editing || draft.question;
  byId('questionTitle').textContent = current ? current.label : draft.receipt ? '填写已结束' : '已完成逐项填写';
  byId('question').textContent = current ? current.question : '请在右侧检查规范值，再启动只读 AI 核查。';
  byId('choices').replaceChildren();
  if (current && !draft.receipt) {
    for (const option of current.options) byId('choices').append(button(option.label, () => answer(current, option.label)));
  }
  byId('customForm').hidden = !current || Boolean(draft.receipt);
  byId('aiState').textContent = draft.ai_enabled ? 'DeepSeek 核查端口已启用；仅在你同意并点击核查后调用。' : 'DeepSeek 推理未启用。可以先填写，但不能把本地规则回放冒称为 AI 核查。';
  rows(byId('answers'), draft.fields.filter(field => draft.answers[field.id]).map(field => ({...field, ...draft.answers[field.id]})), !draft.receipt);
  byId('reviewControls').hidden = draft.completed !== draft.total || Boolean(draft.receipt) || Boolean(editing);
  byId('verify').disabled = busy || !byId('consent').checked;
  byId('humanReview').hidden = !draft.review || Boolean(draft.receipt) || Boolean(editing);
  if (draft.review && !draft.receipt) {
    const review = draft.review;
    byId('model').textContent = `核查子 Agent：${review.agent_role} · ${review.model} · 不改变事实，无提交权限`;
    rows(byId('reviewTable'), review.fields, true);
    byId('unknowns').textContent = review.unknown_fields.length ? '以下项仍为未知，不会自动补全：' + review.unknown_fields.map(id => draft.fields.find(field => field.id === id).label).join('、') : '没有未填写的选项；这不等于材料真实、身份已验证或办理资格通过。';
    const previous = byId('branch').value;
    byId('branch').replaceChildren();
    for (const branch of review.branches) {
      const option = element('option', branch.label);
      option.value = branch.id;
      byId('branch').append(option);
    }
    if (!review.branches.length) {
      const option = element('option', '当前资料不足，没有已确认适用分支');
      option.value = '';
      byId('branch').append(option);
    } else if (review.branches.some(branch => branch.id === previous)) byId('branch').value = previous;
  }
  byId('submit').disabled = busy || !draft.review || !byId('ack').checked || !byId('branch').value || Boolean(editing);
  byId('result').hidden = !draft.receipt;
}
function renderResult(output) {
  draft = output.form;
  byId('receipt').textContent = '本地咨询编号：' + output.receipt.task_id + '。未向任何正式政务系统提交。';
  const host = byId('resultBody');
  host.replaceChildren();
  const answer = output.answer;
  host.append(element('p', '当前状态：' + (answer.status === 'partial' ? '部分信息已确认，仍有待核实项' : answer.status)));
  for (const stage of answer.stages || []) {
    host.append(element('h3', stage.label || stage.id || '办理环节'));
    for (const item of stage.items || []) {
      host.append(element('p', (item.action || '') + ' ' + (item.material || item.requirement_id || '')));
      for (const citation of item.evidence || []) host.append(element('blockquote', citation.quote));
    }
  }
  if (answer.paused?.length) host.append(element('p', '部分要求因当前冲突或更新暂停，不得继续套用旧结论。'));
  if (answer.pending?.length) host.append(element('p', '部分依据或用户情况仍待核实，详见下方结构化记录。'));
  const details = element('details');
  details.append(element('summary', '查看带出处的完整结果'), element('pre', JSON.stringify(answer, null, 2)));
  host.append(details);
}
async function resume() {
  draft = await request('/api/forms/state'); editing = null;
  if (draft.receipt) {
    const history = await request('/api/history?task_id=' + encodeURIComponent(draft.receipt.task_id));
    renderResult({form: draft, receipt: draft.receipt, answer: {...history.original_answer, ...history.current_availability}});
  }
}
byId('enter').addEventListener('click', () => action(async () => {
  try { await request('/api/me'); } catch { await request('/login', {role: 'citizen'}); }
  try { await resume(); }
  catch { draft = await request('/api/forms/start', {}); }
  notify('已进入本机会话，按字段逐项填写。');
}));
byId('resume').addEventListener('click', () => action(resume));
byId('restart').addEventListener('click', () => action(async () => {
  if (!window.confirm('放弃当前草稿并重新填写？已提交的本地咨询记录不会删除。')) return;
  draft = await request('/api/forms/start', {}); editing = null; byId('ack').checked = false; byId('consent').checked = false;
}));
byId('erase').addEventListener('click', () => action(async () => {
  await request('/api/forms/delete', body()); draft = null; editing = null;
  byId('customAnswer').value = ''; byId('ack').checked = false; byId('consent').checked = false;
  byId('answers').replaceChildren(); byId('reviewTable').replaceChildren(); byId('resultBody').replaceChildren();
  notify('本会话草稿已删除，已提交咨询记录仍保留在原流程中。');
}));
byId('logout').addEventListener('click', () => action(async () => {
  await request('/logout', {}); window.location.replace('/guided-form');
}));
byId('customForm').addEventListener('submit', event => {
  event.preventDefault(); action(() => answer(editing || draft.question, byId('customAnswer').value));
});
byId('consent').addEventListener('change', render);
byId('ack').addEventListener('change', render);
byId('branch').addEventListener('change', render);
byId('verify').addEventListener('click', () => action(async () => {
  draft = await request('/api/forms/review', body({consent: byId('consent').checked}));
  byId('ack').checked = false; notify('AI 核查已完成，请逐项检查后再确认提交。');
}));
byId('submit').addEventListener('click', () => action(async () => {
  const output = await request('/api/forms/submit', body({review_id: draft.review.review_id, branch_id: byId('branch').value, acknowledge: byId('ack').checked}));
  renderResult(output); notify('仅提交到本地模拟咨询流程，未提交正式申请。');
}));
byId('refreshResult').addEventListener('click', () => action(async () => {
  const history = await request('/api/history?task_id=' + encodeURIComponent(draft.receipt.task_id));
  renderResult({form: draft, receipt: draft.receipt, answer: {...history.original_answer, ...history.current_availability}});
}));
