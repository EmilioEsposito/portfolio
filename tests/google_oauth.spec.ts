import { test, expect } from '@playwright/test';

test('Google authentication flow', async ({ page }) => {
  await page.goto('http://localhost:3000/auth/google');
  await page.getByText('error').click();
  await page.locator('nextjs-portal > div > div').first().click();
  await page.getByRole('button', { name: 'Close' }).click();
  await page.getByRole('button', { name: 'Connect with Google' }).click();
  await page.getByRole('textbox', { name: 'Email or phone' }).click();
  await page.getByRole('textbox', { name: 'Email or phone' }).fill('espo412@gmail.com');
  await page.getByRole('button', { name: 'Next' }).click();
  await page.getByRole('link', { name: 'Try again' }).click();
  await page.getByRole('textbox', { name: 'Email or phone' }).click();
  await page.getByRole('textbox', { name: 'Email or phone' }).fill('espo412@gmaiil.com');
  await page.getByRole('button', { name: 'Next' }).click();

});