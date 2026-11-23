#!/usr/bin/env bash
# Keep tests from one file together. These slow suites account for about half
# of the test execution time on standard GitHub runners (Nov 2026).
set -euo pipefail

slow_files=(
  api/src/tests/test_showing_captured.py
  api/src/tests/test_showing_daily_texts.py
  api/src/tests/test_showing_reminders.py
  api/src/tests/test_showing_residents.py
  api/src/tests/test_showing_team_actions.py
  api/src/tests/test_showing_unit_gate.py
  api/src/tests/test_zillow_lead_bookings.py
  api/src/tests/test_zillow_lead_email_reminder.py
  api/src/tests/test_zillow_lead_moves.py
)

shard=${1:-}
if [[ $# -gt 0 ]]; then
  shift
fi
case "$shard" in
  booking)
    selection=("${slow_files[@]}")
    ;;
  general)
    selection=()
    for file in "${slow_files[@]}"; do
      selection+=("--ignore=$file")
    done
    ;;
  *)
    echo "Usage: $0 {booking|general} [pytest options]" >&2
    exit 2
    ;;
esac
exec uv run pytest -p no:cacheprovider --tb=short -q "${selection[@]}" "$@"
