import { test, expect } from '@playwright/test';

test.describe('FastAPI Example', () => {
  test.beforeEach(async ({ page }) => {
    // Start at the examples page
    console.log('Navigating to /examples...');
    await page.goto('/examples');
    // Wait for the page to be fully loaded
    await page.waitForLoadState('networkidle');
    console.log('Page loaded');
  });

  test('FastAPI implementation functionality', async ({ page }) => {
    console.log('Starting FastAPI implementation test');
    
    // First verify we're on the examples page
    await expect(page.getByRole('heading', { name: 'Examples' })).toBeVisible();
    console.log('Examples page heading found');
    
    // Find and verify the FastAPI example link exists
    const fastApiLink = page.getByText('FastAPI + GraphQL + Neon Postgres Example');
    await expect(fastApiLink).toBeVisible();
    console.log('FastAPI example link found');
    
    // Navigate to FastAPI example
    await fastApiLink.click();
    console.log('Clicked FastAPI example link');
    
    // Wait for navigation and verify we're on the correct page
    await page.waitForURL('**/examples/neon-fastapi');
    await expect(page.getByRole('heading', { name: 'FastAPI + GraphQL + Neon Postgres Example' })).toBeVisible();
    console.log('Navigated to FastAPI example page');
    
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
}); 