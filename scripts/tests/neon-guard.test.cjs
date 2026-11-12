const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const path = require('node:path');
const { test } = require('node:test');

const hook = path.resolve(__dirname, '../../.claude/hooks/neon-guard.sh');

// The desktop app exposes the connector under an opaque ID; other clients use a name.
const PREFIXES = ['mcp__d5ec6ac2-80c4-4061-bd3f-85286460d87c__', 'mcp__Neon__', 'mcp__claude_ai_Neon__'];
const PROJECT = 'sweet-butterfly-10388995';
const PRODUCTION = 'br-small-forest-af9ve5ur';
const SANDBOX = 'br-purple-fog-afgqu99y';

function decision(tool, toolInput, prefix = PREFIXES[0]) {
  const result = spawnSync('bash', [hook], {
    input: JSON.stringify({ tool_name: `${prefix}${tool}`, tool_input: toolInput }),
    encoding: 'utf8',
  });
  assert.equal(result.status, 0, result.stderr);
  if (result.stdout.trim() === '') return 'none';
  return JSON.parse(result.stdout).hookSpecificOutput.permissionDecision;
}

const onProduction = (sql) => ({ sql, project_id: PROJECT, branch_id: PRODUCTION });
const onDefault = (sql) => ({ sql, project_id: PROJECT });
const onSandbox = (sql) => ({ sql, project_id: PROJECT, branch_id: SANDBOX });

const READS = [
  'SELECT 1',
  'select id, updated_at, created_at from agent_conversations order by updated_at desc limit 20',
  '  \n SELECT count(*)\nFROM open_phone_events\nWHERE event_type = \'message.received\';  \n',
  "WITH recent AS (SELECT * FROM emails WHERE received_at > now() - interval '1 day') SELECT count(*) FROM recent",
  "SELECT CASE WHEN deleted_at IS NULL THEN 'live' ELSE 'gone' END AS state FROM contacts",
  "SELECT pg_size_pretty(pg_total_relation_size('agent_conversations'))",
  'SELECT * FROM pg_stat_activity',
  'SHOW server_version',
  'EXPLAIN SELECT * FROM contacts',
  'EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM contacts',
  'TABLE contacts',
];

const NOT_READS = [
  '',
  "INSERT INTO contacts (name) VALUES ('x')",
  'UPDATE contacts SET name = name',
  'DELETE FROM contacts',
  'delete/**/from contacts',
  'TRUNCATE contacts',
  'DROP TABLE contacts',
  'ALTER TABLE contacts ADD COLUMN x int',
  'CREATE TABLE x (id int)',
  'SELECT 1; DELETE FROM contacts',
  'SELECT 1;\nDROP TABLE contacts;',
  'WITH gone AS (DELETE FROM contacts RETURNING *) SELECT count(*) FROM gone',
  'WITH moved AS (UPDATE contacts SET name = name RETURNING *) SELECT * FROM moved',
  'SELECT * INTO contacts_copy FROM contacts',
  'SELECT * FROM contacts FOR UPDATE',
  'SELECT * FROM contacts FOR SHARE',
  'EXPLAIN ANALYZE DELETE FROM contacts',
  'EXPLAIN (ANALYZE) CREATE TABLE x AS SELECT 1',
  "SELECT setval('contacts_id_seq', 1)",
  "SELECT nextval('contacts_id_seq')",
  'SELECT pg_terminate_backend(pid) FROM pg_stat_activity',
  "SELECT set_config('statement_timeout', '0', false)",
  'SELECT pg_advisory_lock(1)',
  'SELECT pg_sleep(600)',
  "SELECT dblink_exec('dbname=neondb', 'drop table contacts')",
  "SELECT query_to_xml('select 1', true, false, '')",
  "SELECT lo_unlink(1)",
  '-- just a read\nSELECT 1',
  'VACUUM contacts',
  "COPY contacts TO PROGRAM 'cat'",
  'SET statement_timeout = 0',
  'DO $$ BEGIN DELETE FROM contacts; END $$',
  'CALL cleanup()',
];

test('read-only SQL runs without a prompt on production', () => {
  for (const sql of READS) {
    for (const prefix of PREFIXES) {
      assert.equal(decision('run_sql', onProduction(sql), prefix), 'allow', sql);
    }
    assert.equal(decision('run_sql', onDefault(sql)), 'allow', sql);
    assert.equal(decision('run_sql', { sql }), 'allow', sql);
  }
});

test('SQL that is not provably read-only asks on production', () => {
  for (const sql of NOT_READS) {
    for (const prefix of PREFIXES) {
      assert.equal(decision('run_sql', onProduction(sql), prefix), 'ask', sql);
    }
    assert.equal(decision('run_sql', onDefault(sql)), 'ask', sql);
    assert.equal(decision('run_sql', { sql }), 'ask', sql);
  }
});

test('writes ask on a project the guard does not list', () => {
  const sql = 'DELETE FROM contacts';
  assert.equal(decision('run_sql', { sql, project_id: 'some-other-project', branch_id: 'br-other-1' }), 'ask');
  assert.equal(decision('run_sql', { sql, branch_id: SANDBOX }), 'ask');
});

test('writes run without a prompt on a non-production branch of a listed project', () => {
  assert.equal(decision('run_sql', onSandbox('DELETE FROM contacts')), 'allow');
  assert.equal(decision('run_sql', onSandbox('SELECT 1')), 'allow');
  assert.equal(decision('run_sql_transaction', { ...onSandbox(undefined), sql_statements: ['DELETE FROM x'] }), 'allow');
});

test('the legacy camelCase arguments are still read', () => {
  const sql = 'DELETE FROM contacts';
  assert.equal(decision('run_sql', { sql, projectId: PROJECT, branchId: PRODUCTION }), 'ask');
  assert.equal(decision('run_sql', { sql, projectId: PROJECT, branchId: SANDBOX }), 'allow');
});

test('transactions always ask on production', () => {
  for (const input of [
    { sql_statements: ['SELECT 1'], project_id: PROJECT, branch_id: PRODUCTION },
    { sql_statements: ['SELECT 1'], project_id: PROJECT },
    { sql_statements: ['SELECT 1'] },
  ]) {
    assert.equal(decision('run_sql_transaction', input), 'ask');
  }
});

test('EXPLAIN is allowed unless ANALYZE would execute a write', () => {
  assert.equal(decision('explain_sql_statement', onProduction('DELETE FROM contacts')), 'allow');
  assert.equal(decision('explain_sql_statement', { ...onProduction('DELETE FROM contacts'), analyze: false }), 'allow');
  assert.equal(decision('explain_sql_statement', { ...onProduction('SELECT * FROM contacts'), analyze: true }), 'allow');
  assert.equal(decision('explain_sql_statement', { ...onProduction('DELETE FROM contacts'), analyze: true }), 'ask');
  assert.equal(decision('explain_sql_statement', { ...onDefault('UPDATE contacts SET name = name'), analyze: true }), 'ask');
  assert.equal(decision('explain_sql_statement', { ...onSandbox('DELETE FROM contacts'), analyze: true }), 'allow');
});

test('destructive and migration tools always ask', () => {
  for (const tool of ['delete_branch', 'delete_project', 'complete_database_migration', 'complete_query_tuning']) {
    assert.equal(decision(tool, { project_id: PROJECT, branch_id: SANDBOX }), 'ask', tool);
  }
});

test('tools the guard does not know get no decision', () => {
  assert.equal(decision('describe_table_schema', { table_name: 'contacts' }), 'none');
});

test('unparseable input fails closed', () => {
  const result = spawnSync('bash', [hook], { input: 'not json', encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(JSON.parse(result.stdout).hookSpecificOutput.permissionDecision, 'ask');
});
