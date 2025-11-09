import { NextResponse } from 'next/server';
import { neon } from '@neondatabase/serverless';
import { Pool } from 'pg';

// Log the DATABASE_URL (with credentials removed) for debugging
const dbUrlForLogging = process.env.DATABASE_URL?.replace(/\/\/[^@]+@/, '//<credentials>@') || 'not set';
console.log('[API] Database URL provided:', dbUrlForLogging);
console.log('[DEBUG] DOCKER_ENV:', process.env.DOCKER_ENV);
console.log('[DEBUG] DATABASE_REQUIRE_SSL:', process.env.DATABASE_REQUIRE_SSL);
console.log('[DEBUG] RAILWAY_ENVIRONMENT_NAME:', process.env.RAILWAY_ENVIRONMENT_NAME);

if (!process.env.DATABASE_URL) {
  console.error('[API] DATABASE_URL is not set');
  throw new Error('DATABASE_URL is required');
}

// Determine if we're using local PostgreSQL or Neon (production)
// Local dev: DATABASE_REQUIRE_SSL=false
// Railway/Production: DATABASE_REQUIRE_SSL not set or true
const isLocalDev = process.env.DATABASE_REQUIRE_SSL?.toLowerCase() === 'false';

console.log('[API] Environment detection:');
console.log('[API]   isLocalDev:', isLocalDev);
console.log('[API]   Will use:', isLocalDev ? 'pg (local PostgreSQL)' : '@neondatabase/serverless (Neon)');

// Use standard pg for local development, @neondatabase/serverless for production
let pgPool: Pool | null = null;
let neonSql: ReturnType<typeof neon> | null = null;

if (isLocalDev) {
  // Local development: use standard PostgreSQL client
  pgPool = new Pool({
    connectionString: process.env.DATABASE_URL,
    ssl: false,
    max: 10,
    idleTimeoutMillis: 30000,
    connectionTimeoutMillis: 2000,
  });
  console.log('[API] Local PostgreSQL pool created');
} else {
  // Production: use Neon serverless
  neonSql = neon(process.env.DATABASE_URL);
  console.log('[API] Neon serverless client created');
}

export async function GET() {
  console.log('[API] GET /api/examples called');
  
  try {
    console.log('[API] Attempting to fetch examples...');
    
    let rows: any[] = [];
    
    if (isLocalDev && pgPool) {
      // Local development: use pg Pool
      console.log('[API] Using local PostgreSQL...');
      const result = await pgPool.query(`
        SELECT id, title, content, created_at 
        FROM example_neon1 
        ORDER BY created_at DESC
      `);
      rows = result.rows;
    } else if (neonSql) {
      // Production: use Neon serverless
      console.log('[API] Using Neon serverless...');
      const neonResult = await neonSql`
        SELECT id, title, content, created_at 
        FROM example_neon1 
        ORDER BY created_at DESC
      `;
      rows = Array.from(neonResult as any);
    } else {
      throw new Error('No database client available');
    }
    
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
    
    let newRow: any = null;
    
    if (isLocalDev && pgPool) {
      // Local development: use pg Pool
      console.log('[API] Using local PostgreSQL...');
      const result = await pgPool.query(
        'INSERT INTO example_neon1 (title, content) VALUES ($1, $2) RETURNING id, title, content, created_at',
        [title, content]
      );
      newRow = result.rows[0];
    } else if (neonSql) {
      // Production: use Neon serverless
      console.log('[API] Using Neon serverless...');
      const neonResult = await neonSql`
        INSERT INTO example_neon1 (title, content)
        VALUES (${title}, ${content})
        RETURNING id, title, content, created_at
      `;
      newRow = Array.from(neonResult as any)[0];
    } else {
      throw new Error('No database client available');
    }
    
    console.log('[API] Successfully created example:', newRow);
    return NextResponse.json(newRow);
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