async (page) => {
  await page.getByText(/"message_code".*"NO_KNOWLEDGE"/).last().scrollIntoViewIfNeeded();
  await page.screenshot({path:'output/playwright/m1-native-recovery.png',scale:'css'});
}
