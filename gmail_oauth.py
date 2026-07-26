"""
The web version of the Gmail login.

The CLI (gmail_client.py) uses a "Desktop app" OAuth client, which is allowed
to pop open a browser on the same machine and listen on localhost. A web app
can't do that - the user might be anywhere - so this module uses the "Web
application" flow instead:

    1. We send the user to Google with a link.
    2. They approve read-only access to their Gmail.
    3. Google sends them back to /gmail/callback with a code.
    4. We swap that code for a token and save it against their account.

Once a user is connected we reuse gmail_client.fetch_unread_emails() exactly
as the CLI does - only the logging-in part is different.
"""

import json
import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

# Same read-only scope the CLI asks for: we can view emails and nothing else.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# --------------------

def _client_config() -> dict:
    """Build the config dict google-auth expects, from environment variables.

    This is the same information that lives inside a downloaded
    credentials.json, just kept in .env instead so the web client and the
    CLI's desktop client don't fight over the same filename.
    """

    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")

    if not client_id or not client_secret:
        raise RuntimeError(
            "GOOGLE_CLIENT_ID and/or GOOGLE_CLIENT_SECRET are missing from .env. "
            "See the README for how to create a Web application OAuth client."
        )

    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }

# --------------------

def build_flow(redirect_uri: str) -> Flow:
    """Create the OAuth flow object used by both halves of the handshake
    (sending the user to Google, and handling them coming back)."""

    return Flow.from_client_config(_client_config(), scopes=SCOPES, redirect_uri=redirect_uri)

# --------------------

def build_service(token_json: str):
    """Turn a saved token back into a usable Gmail API client.

    Returns (service, refreshed_token_json). Access tokens expire after about
    an hour, so if we had to refresh, the second item is the updated token
    JSON that the caller should save - otherwise it's None.
    """

    creds = Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)
    refreshed_token_json = None

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            # Expired, but we can quietly get a new access token without
            # bothering the user.
            creds.refresh(Request())
            refreshed_token_json = creds.to_json()
        else:
            # No refresh token, or the user revoked access from their Google
            # account settings. Only fix is to connect again.
            raise RuntimeError(
                "This Gmail connection is no longer valid. Please reconnect your account."
            )

    return build("gmail", "v1", credentials=creds), refreshed_token_json

# --------------------

def get_connected_address(service) -> str:
    """Ask Gmail which address we're actually connected to, so the dashboard
    can show it. Useful when someone's Yums login and their Gmail differ."""

    profile = service.users().getProfile(userId="me").execute()
    return profile.get("emailAddress", "")
