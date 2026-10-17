async (page) => {
  await page.getByRole('button', {name:'执行',exact:true}).click();
  await page.getByRole('textbox', {name:'发送消息...',exact:true}).fill('空白技术验收：按真实群众身份检索空知识库，保留完整工具结果并停止。');
  await page.getByRole('button', {name:'发送',exact:true}).click();
}
