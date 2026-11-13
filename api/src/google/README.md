# Google API Integration

This directory contains utilities for interacting with Google APIs, specifically Google Sheets and Gmail.

## Setup

1. Create a Google Cloud Project at [Google Cloud Console](https://console.cloud.google.com/)
2. Enable the APIs you need:
   - Google Sheets API
   - Gmail API
   - Google Drive API
   - Google Calendar API
   - Google Docs API
   (Enable more as needed)

### Service Account Setup

1. Go to "IAM & Admin" > "Service Accounts"
2. Create a new service account with a descriptive name (e.g., "Portfolio App Service Account")
3. Grant necessary roles:
   - "Editor" role is sufficient for most operations
   - Additional roles can be added if needed for specific GCP resources
4. Create and download the JSON key
5. Prepare the credentials for your `.env` file:
   ```bash
   # From the project root
   python api/google/scripts/prepare_credentials.py path/to/downloaded-service-account.json
   ```
   This will output a properly formatted line to add to your `.env` file.

6. Add the generated line to your `.env` file, along with any other needed variables:
   ```
   # The script will generate this base64 encoded line for you
   GOOGLE_SERVICE_ACCOUNT_CREDENTIALS="eyJ0...
   
   # Add other variables as needed
   GOOGLE_SHEETS_SPREADSHEET_ID=your_spreadsheet_id  # Optional: Only if using Sheets
   ```

### Domain-Wide Delegation (for Google Workspace)

If you need to access Workspace resources (like other users' calendars):

1. Go to your Google Workspace Admin Console
2. Navigate to Security > API Controls > [Domain-Wide Delegation](https://admin.google.com/ac/owl/domainwidedelegation)
3. Click "Add new" and enter:
   - Client ID: Your [service account's client ID](https://console.cloud.google.com/iam-admin/serviceaccounts/details/107124113760936638778?inv=1&invt=AbtXoA&project=portfolio-450200) (not OAuth client ID)
   - OAuth Scopes: (one per line)
     ```
     https://www.googleapis.com/auth/calendar
     https://www.googleapis.com/auth/drive
     https://www.googleapis.com/auth/gmail.send
     https://www.googleapis.com/auth/spreadsheets
     ```
4. In your code, when you need to act as a specific user:
   ```python
   from api.google.auth import get_service_credentials
   from googleapiclient.discovery import build
   # Get credentials and delegate to a user
   credentials = get_service_credentials()
   delegated_credentials = credentials.with_subject('emilio@serniacapital.com')
   
   # Use these credentials with any Google service
   calendar_service = build('calendar', 'v3', credentials=delegated_credentials)
   
   # Now you can access their calendar
   events = calendar_service.events().list(calendarId='primary').execute()
   ```

### Resource Sharing

For non-Workspace resources, share directly with service account:
- For Sheets: Share your spreadsheets
- For Drive: Share folders/files
- For Calendar: Add to calendar sharing
- For Workspace resources: Use domain-wide delegation as shown above

### OAuth 2.0 Setup (for user-level access)

Some APIs (like Gmail for sending from your personal account) require user-level OAuth:

1. Go to "APIs & Services" > "Credentials"
2. Create OAuth client ID (Web application)
3. Add authorized redirect URIs:
   - Development: `http://localhost:3000/api/google/auth/callback`
   - Preview: `https://dev.eesposito.com/api/google/auth/callback`
   - Production: `https://eesposito.com/api/google/auth/callback`
4. Add to `.env`:
   ```
   GOOGLE_OAUTH_CLIENT_ID=your_client_id
   GOOGLE_OAUTH_CLIENT_SECRET=your_client_secret
   GOOGLE_OAUTH_REDIRECT_URI=https://eesposito.com/api/google/auth/callback
   GOOGLE_GMAIL_CREDENTIALS=null # Will be populated after OAuth flow
   ```

## Usage

### Service Account Authentication

```python
from api.src.google.common.service_account_auth import get_service_credentials
from googleapiclient.discovery import build
from dotenv import load_dotenv
load_dotenv()

# Get credentials that can be used across Google services
credentials = get_service_credentials()

# Use with any Google API
sheets_service = build('sheets', 'v4', credentials=credentials)
drive_service = build('drive', 'v3', credentials=credentials)
gmail_service = build('gmail', 'v1', credentials=credentials)
calendar_service = build('calendar', 'v3', credentials=credentials)
```

### Google Sheets

```python
from api.src.google.sheets import get_sheet_as_json

# Get all contacts
contacts = get_sheet_as_json(spreadsheet_id=os.getenv('GOOGLE_SHEETS_SPREADSHEET_ID'))
```

### Gmail

```python
from api.src.google.gmail.service import send_email
from api.src.google.common.service_account_auth import get_delegated_credentials
# For sending from service account
await send_email(
        to="espo412@gmail.com",
        subject="Test email",
        message_text="This is a test email",
        credentials=get_delegated_credentials(
            user_email="emilio@serniacapital.com", scopes=["https://mail.google.com"]
        ),
    )

```

## API Endpoints

Coming soon...

## Directory Structure

```
api/google/
├── README.md           # This file
├── __init__.py        # Package initialization
├── auth.py            # Shared authentication utilities
├── routes.py          # FastAPI router and endpoints (coming soon)
├── gmail.py           # Gmail API utilities
└── sheets.py          # Google Sheets API utilities
```

## Best Practices

1. **Service Account vs OAuth**:
   - Use service account for backend/automated operations
   - Use OAuth for actions that need user context (e.g., sending from personal email)

2. **Scopes**:
   - Request only the scopes you need
   - Document scope changes in comments

3. **Security**:
   - Never commit credentials to version control
   - Use environment variables for all secrets
   - Regularly rotate service account keys

4. **Resource Sharing**:
   - Only share necessary resources with service account
   - Use most restrictive permissions possible 


## Gmail PubSub Notifications

Gmail PubSub notifications are used to notify the server when new emails arrive.

It requires a watch to be set up and refreshed at least every 7 days. There is a cron job calling the `/api/google/gmail/watch/refresh` endpoint every day at 7am UTC.

You can monitor the [gmail-notifications-sub here.](https://console.cloud.google.com/cloudpubsub/subscription/detail/gmail-notifications-sub?inv=1&invt=Abpamw&project=portfolio-450200)
### Push authentication and mailbox boundary

The FastAPI service must explicitly configure both non-secret variables:

- `PUBSUB_PUSH_SERVICE_ACCOUNT_EMAIL`: exactly the service account selected under
  the subscription's push authentication settings (`pushConfig.oidcToken.serviceAccountEmail`).
  This is the push identity, which need not be the application's delegated Gmail account.
- `PUBSUB_AUDIENCE_URL`: exactly `pushConfig.oidcToken.audience`, or the complete
  push endpoint URL when the subscription has no audience override. Query strings
  and trailing slashes are significant; request headers never select the audience.

Read the subscription settings before deployment and set these variables on the
owning API service. Missing or blank configuration returns 503 without processing
notifications. Invalid signatures, expiry, issuer, audience, service-account email,
missing required claims, or an `email_verified` claim other than boolean `true`
return 401. See [Google's push authentication guidance](https://cloud.google.com/pubsub/docs/authenticate-push-subscriptions).

The watch and notification processor share `GMAIL_WATCH_MAILBOX` in
`gmail/config.py`. Notifications for any other mailbox return 403 before delegated
credentials, Gmail reads, database writes, or downstream queue work. The mailbox
is intentionally fixed to the existing single-mailbox ingestion workflow; changing
it requires reviewing the Zillow shared-copy workflow as well. Watch refresh and
successful-delivery acknowledgements are otherwise unchanged.

Validate a rollout with synthetic negative requests and passive delivery telemetry.
Do not publish test messages to the live topic or send real emails to exercise it.
The synthetic regression suite is `api/src/tests/test_pubsub_identity.py`.
