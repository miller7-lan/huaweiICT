async (page) => {
  await page.getByRole('button', {name:'执行',exact:true}).click();
  await page.getByRole('textbox', {name:'发送消息...',exact:true}).fill('空白技术异常验收：按真实身份检查环境，ERROR 后立即停止，不调用子 Agent 或业务工具。');
  const pending = page.waitForResponse(response => new URL(response.url()).pathname === '/api/agent/run' && response.request().method() === 'POST');
  await page.getByRole('button', {name:'发送',exact:true}).click();
  const response = await pending;
  const raw = (await response.body()).toString('utf8');
  if(response.status()!==200){
    let error={};try{error=JSON.parse(raw)}catch{}
    return {http_status:response.status(),error_code:error.code,error_detail:error.detail,
      request_header_names:Object.keys(response.request().headers()),body_keys:Object.keys(response.request().postDataJSON())};
  }
  const events = raw.split('\n').filter(line=>line.startsWith('data:') && line.slice(5).trim()).map(line=>JSON.parse(line.slice(5).trim()));
  const finals = events.filter(e=>e.type==='final_answer' && !e.depth).map(e=>e.content);
  const value = finals.length===1 && typeof finals[0]==='object' ? finals[0] : JSON.parse(finals.join(''));
  await page.getByText(JSON.stringify(value)).first().waitFor().catch(async()=>{
    await page.getByText(new RegExp('"message_code".*'+value.message_code)).first().waitFor();
  });
  const idHeader = response.headers()['conversation_id'];
  const created = events.find(e=>e.type==='conversation_created');
  let createdId = null;
  if(created){try{createdId=typeof created.content==='object'?created.content.conversation_id:JSON.parse(created.content).conversation_id;}catch{}}
  const requestId = response.request().postDataJSON().conversation_id;
  const conversationId = idHeader && /^\d+$/.test(idHeader)?Number(idHeader):(createdId || (Number.isInteger(requestId)&&requestId>0?requestId:null));
  const screenshot = 'output/playwright/m1-native-'+value.message_code+'.png';
  await page.screenshot({path:screenshot,scale:'css'});
  return {http_status:response.status(),conversation_id:conversationId,events,result:value,screenshot};
}
