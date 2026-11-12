#!/bin/bash
# Guard Neon operations. Reads run without a prompt, production included. Anything that
# can change production, or that this script cannot prove is a read, asks first.
#
# branch_id is optional and Neon uses the default (production) branch when it is omitted,
# so a call with no branch_id counts as production.
#
# The SQL check below stops accidental writes. It is not the security boundary: that is
# the connector's read-only mode (see .claude/README.md), which Neon enforces server-side.

# Byte-wise text handling, so lowercasing does not depend on the caller's locale.
export LC_ALL=C

# "<project id>:<production branch id>". SQL that is not provably read-only runs without
# a prompt only on a non-production branch of a project listed here.
SANDBOX_PROJECTS=(
  "sweet-butterfly-10388995:br-small-forest-af9ve5ur" # portfolio / production
)

# A statement containing any of these words is not treated as a read. Inside a statement
# that starts with a read keyword, these are the only ways to write or take row locks:
# data-modifying CTEs, SELECT INTO, FOR UPDATE/SHARE, and EXPLAIN ANALYZE of a write.
# The rest of the list is cheap insurance.
WRITE_WORDS=(
  insert update delete merge into create execute declare share
  truncate drop alter grant revoke copy
)

# Functions with side effects that a SELECT can call. Matched as word prefixes.
SIDE_EFFECT_FUNCTION_PREFIXES=(
  nextval setval set_config
  pg_terminate_backend pg_cancel_backend pg_reload_conf pg_rotate_logfile pg_promote
  pg_advisory pg_try_advisory pg_sleep pg_notify
  pg_stat_reset pg_stat_statements_reset
  pg_create_ pg_drop_ pg_replication_ pg_logical_ pg_switch_ pg_wal_replay
  pg_backup_ pg_export_ pg_import_ pg_background
  lo_ lowrite dblink
  query_to_xml cursor_to_xml table_to_xml schema_to_xml database_to_xml
  http_get http_post http_put http_patch http_delete http_head
)

decide() {
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"%s","permissionDecisionReason":"%s"}}\n' "$1" "$2"
  exit 0
}

# True only when the statement is provably a read. Comments and string literals are not
# parsed, so a write word inside one makes the statement ask; that errs on the safe side.
is_read_only_sql() {
  local sql="$1" lower words first word prefix

  # Drop trailing whitespace and one trailing semicolon. Any other semicolon could start
  # a second statement.
  sql="${sql%"${sql##*[![:space:]]}"}"
  sql="${sql%;}"
  [[ "$sql" == *";"* ]] && return 1

  lower=$(printf ' %s ' "$sql" | tr 'A-Z' 'a-z')

  # U&"..." spells an identifier with Unicode escapes, which would hide a name from the
  # word lists below.
  [[ "$lower" == *'u&"'* ]] && return 1

  # Reduce to space-separated words, with a space at both ends.
  words=$(printf '%s' "$lower" | tr -cs 'a-z0-9_' ' ')

  first="${words# }"
  first="${first%% *}"
  case "$first" in
    select | with | show | explain | values | table) ;;
    *) return 1 ;;
  esac

  for word in "${WRITE_WORDS[@]}"; do
    [[ "$words" == *" $word "* ]] && return 1
  done
  for prefix in "${SIDE_EFFECT_FUNCTION_PREFIXES[@]}"; do
    [[ "$words" == *" $prefix"* ]] && return 1
  done
  return 0
}

# True when the call names a non-production branch of a project in SANDBOX_PROJECTS.
is_sandbox_branch() {
  local project_id="$1" branch_id="$2" entry
  [ -n "$project_id" ] && [ -n "$branch_id" ] || return 1
  for entry in "${SANDBOX_PROJECTS[@]}"; do
    if [ "${entry%%:*}" = "$project_id" ] && [ "${entry#*:}" != "$branch_id" ]; then
      return 0
    fi
  done
  return 1
}

# Fail closed: without jq the call cannot be inspected.
command -v jq >/dev/null 2>&1 || decide ask "Neon guard cannot inspect this call (jq missing) - requires confirmation"

INPUT=$(cat)
# One field per line: tool name, project, branch, whether EXPLAIN will execute, then the SQL
# as a JSON string so that newlines in it stay on one line. An ID with any character outside
# the ID alphabet reads as empty, so it cannot span lines or match a listed project.
FIELDS=$(printf '%s' "$INPUT" | jq -r '
  def id: if type == "string" and test("\\A[A-Za-z0-9_-]+\\z") then . else "" end;
  def text: if type == "string" then . else "" end;
  (.tool_name | text | gsub("\\s"; "")),
  (.tool_input.project_id // .tool_input.projectId | id),
  (.tool_input.branch_id // .tool_input.branchId | id),
  (if (.tool_input.analyze // false) == false then "plan" else "execute" end),
  (.tool_input.sql | text | tojson)
' 2>/dev/null) || decide ask "Neon guard cannot parse this call - requires confirmation"

{
  IFS= read -r TOOL_NAME
  IFS= read -r PROJECT_ID
  IFS= read -r BRANCH_ID
  IFS= read -r EXPLAIN_MODE
  IFS= read -r SQL_JSON
} <<<"$FIELDS"
SQL=$(printf '%s' "$SQL_JSON" | jq -r '.' 2>/dev/null) || decide ask "Neon guard cannot parse this call - requires confirmation"
ACTION="${TOOL_NAME##*__}"

case "$ACTION" in
  delete_branch | delete_project)
    decide ask "Destructive Neon operation - requires confirmation"
    ;;
  complete_database_migration | complete_query_tuning)
    decide ask "Applies changes to the parent (production) branch - requires confirmation"
    ;;
  explain_sql_statement)
    # Without analyze, EXPLAIN only plans the statement and nothing executes.
    [ "$EXPLAIN_MODE" = "plan" ] && decide allow "EXPLAIN without ANALYZE does not execute the statement"
    ;;
  run_sql) ;;
  run_sql_transaction)
    # Multi-statement transactions are never classified as reads.
    SQL=""
    ;;
  *)
    # Not a tool this guard knows: leave the call to normal permissions.
    exit 0
    ;;
esac

if is_read_only_sql "$SQL"; then
  decide allow "Read-only Neon SQL"
fi

if is_sandbox_branch "$PROJECT_ID" "$BRANCH_ID"; then
  decide allow "Neon SQL on a non-production branch"
fi

if [ -z "$BRANCH_ID" ]; then
  decide ask "Neon SQL that is not provably read-only, with no branch_id (defaults to production) - requires confirmation"
fi
decide ask "Neon SQL that is not provably read-only, on production or an unrecognised project - requires confirmation"
