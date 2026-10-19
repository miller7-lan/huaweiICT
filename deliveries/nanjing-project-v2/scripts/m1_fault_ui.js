async (page) => {
  const pending = page.waitForResponse(response => new URL(response.url()).pathname === '/api/state');
  await page.getByRole('button', {name:'刷新状态',exact:true}).click();
  const response = await pending;
  const value = await response.json();
  await page.getByRole('status').filter({hasText:'ERROR / '+value.message_code}).waitFor();
  const screenshot = 'output/playwright/m1-BT12-'+value.message_code+'.png';
  await page.screenshot({path:screenshot,scale:'css'});
  return {http_status:response.status(), result:value, screenshot,
    role_text:await page.getByText('已核定业务角色：群众',{exact:true}).textContent()};
}
