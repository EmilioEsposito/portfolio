#!/bin/bash

# Load environment variables
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../.env.development.local"

MESSAGE="$1"
GROUP_ID='claude-code'
DELAY="${2:-30}"  # Default 30 second delay

# Wait to see if user dismisses the notification locally
sleep "$DELAY"

# Check for any outstanding notifications for this group
# terminal-notifier -list returns empty if no notifications are present
result=$(terminal-notifier -list "$GROUP_ID" 2>/dev/null)

# If there are still outstanding notifications, user hasn't dismissed them
# This likely means they're away from their computer
if [ -n "$result" ]; then
  terminal-notifier -message "--DEBUG-- SLACK_WEBHOOK_CLAUDE_CODE: $SLACK_WEBHOOK_CLAUDE_CODE" -title "DEBUG"
  # Send Slack notification
  curl -s -X POST -H 'Content-type: application/json' \
    --data "{\"text\":\"$MESSAGE\"}" \
    "$SLACK_WEBHOOK_CLAUDE_CODE" > /dev/null 2>&1
fi
