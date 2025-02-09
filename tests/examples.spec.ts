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
  });

  test('examples page loads with all examples', async ({ page }) => {
    // Check if we're on the examples page
    await expect(page.getByRole('heading', { name: 'Examples' })).toBeVisible();
    
    // Check if both example cards are present
    await expect(page.getByText('FastAPI + GraphQL + Neon Postgres Example')).toBeVisible();
    await expect(page.getByText('Next.js + Neon Postgres Example')).toBeVisible();
  });

  test('FastAPI implementation functionality', async ({ page }) => {
    // Navigate to FastAPI example
    await page.getByText('FastAPI + GraphQL + Neon Postgres Example').click();
    await expect(page.getByRole('heading', { name: 'FastAPI + GraphQL + Neon Postgres Example' })).toBeVisible();
    
    // Test example input and submission
    const testTitle = 'Test Title via FastAPI ' + Date.now();
    const testContent = 'Test content via FastAPI ' + Date.now();
    
    await page.getByPlaceholder('Enter a title...').fill(testTitle);
    await page.getByPlaceholder('Enter content...').fill(testContent);
    await page.getByRole('button', { name: 'Add' }).click();
    
    // Wait for the add request to complete
    const addResponse = await page.waitForResponse(response => 
      response.url().includes('/api/graphql') && 
      response.request().method() === 'POST'
    );
    expect(addResponse.ok()).toBeTruthy();
    
    // Click refresh and wait for response
    await page.getByRole('button', { name: 'Refresh' }).click();
    await expect(page.getByText(testTitle)).toBeVisible();
    await expect(page.getByText(testContent)).toBeVisible();
  });

  test('Next.js implementation functionality', async ({ page }) => {
    // Navigate to Next.js example
    await page.getByText('Next.js + Neon Postgres Example').click();
    await expect(page.getByRole('heading', { name: 'Next.js + Neon Postgres Example' })).toBeVisible();
    
    // Test example input and submission
    const testTitle = 'Test Title via Next.js ' + Date.now();
    const testContent = 'Test content via Next.js ' + Date.now();
    
    await page.getByPlaceholder('Enter a title...').fill(testTitle);
    await page.getByPlaceholder('Enter content...').fill(testContent);
    await page.getByRole('button', { name: 'Add' }).click();
    
    // Wait for the add request to complete and verify it succeeded
    const addResponse = await page.waitForResponse(response => response.url().includes('/api/examples'));
    expect(addResponse.ok()).toBeTruthy();
    
    // Click refresh and wait for response
    await page.getByRole('button', { name: 'Refresh' }).click();
    await expect(page.getByText(testTitle)).toBeVisible();
    await expect(page.getByText(testContent)).toBeVisible();
  });

  test('examples persist between implementations', async ({ page }) => {
    // Navigate to FastAPI example first
    await page.getByText('FastAPI + GraphQL + Neon Postgres Example').click();
    
    // Create example in FastAPI implementation
    const testTitle = 'Cross-implementation test ' + Date.now();
    const testContent = 'Cross-implementation content ' + Date.now();
    
    await page.getByPlaceholder('Enter a title...').fill(testTitle);
    await page.getByPlaceholder('Enter content...').fill(testContent);
    await page.getByRole('button', { name: 'Add' }).click();
    
    // Go back to examples page and then to Next.js implementation
    await page.goto('/examples');
    await page.getByText('Next.js + Neon Postgres Example').click();
    await page.getByRole('button', { name: 'Refresh' }).click();
    
    await expect(page.getByText(testTitle)).toBeVisible();
    await expect(page.getByText(testContent)).toBeVisible();
  });
}); 