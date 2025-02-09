import { NextResponse } from 'next/server';
import { neon } from '@neondatabase/serverless';

const sql = neon(process.env.DATABASE_URL!);

export async function GET() {
  try {
    console.log('Attempting to fetch examples...');
    const rows = await sql`
      SELECT id, title, content, created_at 
      FROM EXAMPLE_NEON1 
      ORDER BY created_at DESC
    `;
    console.log('Successfully fetched examples:', rows);
    return NextResponse.json(rows);
  } catch (error) {
    console.error('Database Error:', error);
    // Log more details about the error
    if (error instanceof Error) {
      console.error('Error name:', error.name);
      console.error('Error message:', error.message);
      console.error('Error stack:', error.stack);
    }
    return NextResponse.json(
      { error: 'Failed to fetch examples', details: error instanceof Error ? error.message : String(error) },
      { status: 500 }
    );
  }
}

export async function POST(request: Request) {
  try {
    console.log('Attempting to create example...');
    const { title, content } = await request.json();
    console.log('Received title:', title);
    console.log('Received content:', content);
    
    const rows = await sql`
      INSERT INTO EXAMPLE_NEON1 (title, content)
      VALUES (${title}, ${content})
      RETURNING id, title, content, created_at
    `;
    
    console.log('Successfully created example:', rows[0]);
    return NextResponse.json(rows[0]);
  } catch (error) {
    console.error('Database Error:', error);
    // Log more details about the error
    if (error instanceof Error) {
      console.error('Error name:', error.name);
      console.error('Error message:', error.message);
      console.error('Error stack:', error.stack);
    }
    return NextResponse.json(
      { error: 'Failed to create example', details: error instanceof Error ? error.message : String(error) },
      { status: 500 }
    );
  }
} 