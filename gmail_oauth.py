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

from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

# gmail.readonly is the same scope the CLI asks for: we can view emails and
# nothing else. userinfo.profile is on top of that, purely so we can show the
# account's Google profile picture in the header - it grants no extra access
# to any email. Drop it from this list and the header falls back to showing
# the user's initial instead.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/userinfo.profile",
]

# Returns the signed-in account's public profile, including "picture".
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

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

    # Note we don't pass SCOPES here. The saved token already records which
    # scopes Google actually granted, and forcing today's list onto a token
    # issued before we started asking for the profile scope would make the
    # refresh complain about scopes the user never agreed to. Anyone who
    # connected earlier keeps working; they just get the initial in the
    # header instead of a photo until they reconnect.
    creds = Credentials.from_authorized_user_info(json.loads(token_json))
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

# --------------------

def get_profile_picture(credentials) -> str:
    """Fetch the URL of the account's Google profile picture.

    Returns "" if there isn't one - which happens for accounts with no photo
    set, and for anyone who connected before we started asking for the
    profile scope. The header falls back to an initial in that case, so this
    swallows any failure rather than blocking the Gmail connection over a
    missing avatar.
    """

    try:
        response = AuthorizedSession(credentials).get(USERINFO_URL, timeout=10)
        response.raise_for_status()
        return response.json().get("picture", "") or ""
    except Exception as error:
        print(f"[get_profile_picture] couldn't fetch avatar: {type(error).__name__}: {error}")
        return ""
