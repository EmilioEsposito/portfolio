import { test, expect } from '@playwright/test';

test('test_multiselect_click', async ({ page }) => {
  await page.goto('http://localhost:3000/examples/multi-select');
  
  // Click the multi-select button to open the popover
  const multiSelectButton = page.getByRole('button', { name: 'React Angular' });
  await multiSelectButton.click();
  
  // Click the Vue option
  const vueOption = page.getByRole('option', { name: 'Vue' });
  await vueOption.click({ force: true });
  
  // Verify the selection was made
  await expect(multiSelectButton).toContainText('Vue');
});
