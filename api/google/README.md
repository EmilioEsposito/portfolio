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
2. Navigate to Security > API Controls > Domain-Wide Delegation
3. Click "Add new" and enter:
   - Client ID: Your service account's client ID (not OAuth client ID)
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
from api.google.auth import get_service_credentials
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
from api.google.sheets import get_contacts_from_sheet

# Get all contacts
contacts = get_contacts_from_sheet(os.getenv('GOOGLE_SHEETS_SPREADSHEET_ID'))
```

### Gmail

```python
from api.google.gmail import send_email, get_oauth_url

# For sending from service account
send_email(
    credentials=get_service_credentials(),
    to="recipient@example.com",
    subject="Test Email",
    message="Hello from the service account!"
)

# For sending from your personal account (requires OAuth)
auth_url = await get_gmail_auth_url()  # First time setup
send_email(
    credentials_json=eval(os.getenv('GOOGLE_GMAIL_CREDENTIALS')),
    to="recipient@example.com",
    subject="Test Email",
    message="Hello from your personal account!"
)
```

## API Endpoints

### Gmail
- `GET /api/google/auth/gmail` - Get OAuth authorization URL
- `POST /api/google/send-email` - Send email (requires auth)

### Sheets
- `GET /api/google/sheets/contacts` - Get contacts from sheet (requires auth)

## Directory Structure

```
api/google/
├── README.md           # This file
├── __init__.py        # Package initialization
├── auth.py            # Shared authentication utilities
├── routes.py          # FastAPI router and endpoints
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