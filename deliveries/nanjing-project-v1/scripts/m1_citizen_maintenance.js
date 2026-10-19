async (page) => {
  const trace = [];
  for (const [name, path, filename] of [
    ['准备空更新', '/api/tools/prepare_update', 'output/playwright/m1-BT11-prepare.png'],
    ['检查发布前提', '/api/tools/publish_update', 'output/playwright/m1-BT11-publish.png']
  ]) {
    const pending = page.waitForResponse(response => new URL(response.url()).pathname === path && response.request().method() === 'POST');
    await page.getByRole('button', {name, exact:true}).click();
    const response = await pending;
    const value = await response.json();
    await page.getByRole('status').filter({hasText:'BLOCKED / FORBIDDEN'}).waitFor();
    await page.screenshot({path:filename,scale:'css'});
    trace.push({operation:path, http_status:response.status(),result:value,screenshot:filename});
  }
  return {passed:trace.every(r=>r.result.status==='BLOCKED' && r.result.message_code==='FORBIDDEN' && r.result.data.role==='citizen'), trace};
}
