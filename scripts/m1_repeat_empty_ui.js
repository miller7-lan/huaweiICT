async (page) => {
  const trace=[];
  for(let attempt=1;attempt<=3;attempt++){
    const pending=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/tools/prepare_update');
    await page.getByRole('button',{name:'准备空更新',exact:true}).click();
    const response=await pending;
    const result=await response.json();
    await page.getByRole('status').filter({hasText:'EMPTY / NO_CHANGES'}).waitFor();
    trace.push({attempt,result});
  }
  await page.screenshot({path:'output/playwright/m1-BT14.png',scale:'css'});
  return {trace,screenshot:'output/playwright/m1-BT14.png'};
}
