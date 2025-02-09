import { NextResponse } from 'next/server';
import { neon } from '@neondatabase/serverless';

// Log the DATABASE_URL (with credentials removed) for debugging
const dbUrlForLogging = process.env.DATABASE_URL?.replace(/\/\/[^@]+@/, '//<credentials>@') || 'not set';
console.log('[API] Database URL format:', dbUrlForLogging);

if (!process.env.DATABASE_URL) {
  console.error('[API] DATABASE_URL is not set');
  throw new Error('DATABASE_URL is required');
}

const sql = neon(process.env.DATABASE_URL);

export async function GET() {
  console.log('[API] GET /api/examples called');
  
  try {
    console.log('[API] Attempting to fetch examples...');
    
    // Test database connection first
    try {
      const testResult = await sql`SELECT 1 as test`;
      console.log('[API] Database connection test result:', testResult);
    } catch (connError) {
      console.error('[API] Database connection test failed:', connError);
      return NextResponse.json({
        error: 'Database connection failed',
        details: connError instanceof Error ? connError.message : String(connError),
        dbUrl: dbUrlForLogging
      }, { status: 500 });
    }
    
    console.log('[API] Executing main query...');
    const rows = await sql`
      SELECT id, title, content, created_at 
      FROM example_neon1 
      ORDER BY created_at DESC
    `;
    console.log('[API] Successfully fetched examples:', rows);
    return NextResponse.json(rows);
  } catch (error) {
    console.error('[API] Database Error:', error);
    // Log more details about the error
    if (error instanceof Error) {
      console.error('[API] Error name:', error.name);
      console.error('[API] Error message:', error.message);
      console.error('[API] Error stack:', error.stack);
    }
    
    // Return a more specific error message
    const errorMessage = error instanceof Error ? error.message : String(error);
    return NextResponse.json({
      error: 'Failed to fetch examples',
      details: errorMessage,
      dbUrl: dbUrlForLogging,
      timestamp: new Date().toISOString()
    }, { 
      status: 500,
      headers: {
        'Content-Type': 'application/json'
      }
    });
  }
}

export async function POST(request: Request) {
  console.log('[API] POST /api/examples called');
  
  try {
    console.log('[API] Attempting to create example...');
    const { title, content } = await request.json();
    console.log('[API] Received data:', { title, content });
    
    // Test database connection first
    try {
      const testResult = await sql`SELECT 1 as test`;
      console.log('[API] Database connection test result:', testResult);
    } catch (connError) {
      console.error('[API] Database connection test failed:', connError);
      return NextResponse.json({
        error: 'Database connection failed',
        details: connError instanceof Error ? connError.message : String(connError),
        dbUrl: dbUrlForLogging
      }, { status: 500 });
    }
    
    console.log('[API] Executing insert query...');
    const rows = await sql`
      INSERT INTO example_neon1 (title, content)
      VALUES (${title}, ${content})
      RETURNING id, title, content, created_at
    `;
    
    console.log('[API] Successfully created example:', rows[0]);
    return NextResponse.json(rows[0]);
  } catch (error) {
    console.error('[API] Database Error:', error);
    // Log more details about the error
    if (error instanceof Error) {
      console.error('[API] Error name:', error.name);
      console.error('[API] Error message:', error.message);
      console.error('[API] Error stack:', error.stack);
    }
    
    // Return a more specific error message
    const errorMessage = error instanceof Error ? error.message : String(error);
    return NextResponse.json({
      error: 'Failed to create example',
      details: errorMessage,
      dbUrl: dbUrlForLogging,
      timestamp: new Date().toISOString()
    }, { 
      status: 500,
      headers: {
        'Content-Type': 'application/json'
      }
    });
  }
} 