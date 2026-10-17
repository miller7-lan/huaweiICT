async (page) => {
  let agentRuns = 0;
  const count = request => { if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/agent/run') agentRuns++; };
  page.on('request', count);
  try {
    await page.getByRole('button', { name: '执行', exact: true }).click();
    const input = page.getByRole('textbox', { name: '发送消息...', exact: true });
    const send = page.getByRole('button', { name: '发送', exact: true });
    await input.fill('');
    const emptyDisabled = await send.isDisabled();
    await input.press('Enter');
    await input.fill(' \n\t');
    const whitespaceDisabled = await send.isDisabled();
    await input.press('Enter');
    await input.fill('');
    await page.waitForTimeout(800);
    await page.screenshot({path:'output/playwright/m1-BT02.png',scale:'css'});
    return {empty_disabled:emptyDisabled, whitespace_disabled:whitespaceDisabled,
      agent_run_requests_before:0, agent_run_requests_after:agentRuns,
      model_dispatch_count:agentRuns, assertion:emptyDisabled && whitespaceDisabled && agentRuns===0};
  } finally { page.removeListener('request', count); }
}
