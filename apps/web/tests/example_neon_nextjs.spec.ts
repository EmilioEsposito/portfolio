import { test, expect } from '@playwright/test';

// Ensure dev server is running
test.beforeAll(async () => {
  // Log test environment
  console.log('Starting examples tests...');
  console.log('Make sure you have run `pnpm dev` in a separate terminal');
});

test.describe('Examples Page', () => {
  test.beforeEach(async ({ page }) => {
    // Start at the examples page
    console.log('Navigating to /examples...');
    await page.goto('/examples');
    // Wait for the page to be fully loaded
    await page.waitForLoadState('networkidle');
    console.log('Page loaded');
  });

  test('examples page loads with all examples', async ({ page }) => {
    // Check if we're on the examples page
    await expect(page.getByRole('heading', { name: 'Examples' })).toBeVisible();
    
    // Check if both example cards are present
    await expect(page.getByText('FastAPI + GraphQL + Neon Postgres Example')).toBeVisible();
    await expect(page.getByText('Next.js + Neon Postgres Example')).toBeVisible();
  });

  test('Next.js implementation functionality', async ({ page }) => {
    console.log('Starting Next.js implementation test');
    
    // First verify we're on the examples page
    await expect(page.getByRole('heading', { name: 'Examples' })).toBeVisible();
    console.log('Examples page heading found');
    
    // Log all links on the page for debugging
    const links = await page.getByRole('link').all();
    for (const link of links) {
      const text = await link.textContent();
      console.log('Found link:', text);
    }
    
    // Find and verify the Next.js example link exists
    const nextJsLink = page.getByText('Next.js + Neon Postgres Example');
    await expect(nextJsLink).toBeVisible();
    console.log('Next.js example link found');
    
    // Navigate to Next.js example
    await nextJsLink.click();
    console.log('Clicked Next.js example link');
    
    // Wait for navigation and verify we're on the correct page
    await page.waitForURL('**/examples/neon-nextjs');
    await expect(page.getByRole('heading', { name: 'Next.js + Neon Postgres Example' })).toBeVisible();
    console.log('Navigated to Next.js example page');
    
    // Test example input and submission
    const testTitle = 'Test Title via Next.js ' + Date.now();
    const testContent = 'Test content via Next.js ' + Date.now();
    
    await page.getByPlaceholder('Enter a title...').fill(testTitle);
    await page.getByPlaceholder('Enter content...').fill(testContent);
    await page.getByRole('button', { name: 'Add' }).click();
    
    // Wait for the add request to complete and verify it succeeded
    const addResponse = await page.waitForResponse(response => response.url().includes('/api/examples'));
    if (!addResponse.ok()) {
      const errorData = await addResponse.json();
      console.error('API Error Response:', errorData);
    }
    expect(addResponse.ok()).toBeTruthy();
    
    // Click refresh and wait for response
    await page.getByRole('button', { name: 'Refresh' }).click();
    await expect(page.getByText(testTitle)).toBeVisible();
    await expect(page.getByText(testContent)).toBeVisible();
  });
}); 