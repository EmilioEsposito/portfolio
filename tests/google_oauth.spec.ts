import { test, expect } from '@playwright/test';

test('Google authentication flow', async ({ page }) => {
  // Start from the Google auth page
  await page.goto('http://localhost:3000/auth/google');
  
  // Click the connect button
  await page.getByRole('button', { name: 'Connect with Google' }).click();
  
  // Mock the successful auth check response
  await page.route('/api/google/auth/check', async (route) => {
    await route.fulfill({
      status: 200,
      body: JSON.stringify({
        authenticated: true,
        user_id: 'test@example.com',
        scopes: ['https://www.googleapis.com/auth/gmail.readonly']
      })
    });
  });

  // Wait for navigation to the success page
  await page.waitForURL('**/auth/success', { timeout: 5000 });
  
  // Verify we're on the success page
  await expect(page).toHaveURL(/.*\/auth\/success$/);
  
  // Verify success message is shown
  await expect(page.getByText('Authentication Successful')).toBeVisible();
  
  // Verify the mocked email is displayed
  await expect(page.getByText('test@example.com')).toBeVisible();
});