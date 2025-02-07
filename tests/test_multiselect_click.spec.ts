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
  
  // Reopen the popover
  await page.getByRole('button', { name: /Vue/ }).click();
  
  // Click Clear and verify selection is removed
  await page.getByRole('option', { name: 'Clear' }).click({ force: true });
  
  // After clearing, the button should show the placeholder
  await expect(page.getByRole('button', { name: 'Select frameworks' })).toBeVisible();
  
  // Reopen and test Close button
  await page.getByRole('button', { name: 'Select frameworks' }).click();
  await page.getByRole('option', { name: 'Close' }).click({ force: true });
  await expect(page.getByRole('listbox')).not.toBeVisible();
});
