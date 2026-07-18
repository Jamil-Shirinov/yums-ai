"""
Everything related to talking to Gmail: logging in (OAuth2) and fetching unread emails from the inbox.
"""

import base64
import os
import re
from html import unescape

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from models import EmailMessage

# The app cal only view the user's email. It doesn't have the permission to send, delete, or modify anything :D
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CREDENTIALS_FILE = "credentials.json"   # downloaded from gcloud Console
TOKEN_FILE = "token.json"               # created automatically after the first login

# --------------------

def get_gmail_service():
    """Authenticate with Gmail and return an API client object I can
    use to make requests. Handles caching/refreshing the login token so the user
    only has to approve access in the browser just once.
    Pretty straightforward, isn't it?"""

    creds = None

    # If we've logged in before, reuse the saved token.
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    # If there's no valid saved token, get a new one.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # Token expired but we can silently refresh it.
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                raise FileNotFoundError(
                    f"Couldn't find '{CREDENTIALS_FILE}'. Follow the README's "
                    "Google Cloud Console setup steps and download your OAuth "
                    "credentials to this folder first."
                )
            # Opens a browser window for the user to log in and approve access
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        # Save the token for next time so we don'thave to repeat this
        with open(TOKEN_FILE, "w") as token_file:
            token_file.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)

# --------------------

def _strip_html(html_text: str) -> str:
    """Lighty html-to-text conversion. It removes tags and decodes entities
    like &amp; -> &. Good enough for summarization."""

    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html_text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)      # strip remaining tags
    text = unescape(text)                       # &amp; -> &, &nbsp; -> " ", etc.
    text = re.sub(r"\s+", " ", text).strip()      # collapse whitespace
    return text

# --------------------

def _decode_body_part(data: str) -> str:
    """Gmail encodes message bodies in URL-safe base64. This decodes one
    part of a message into plain text."""

    if not data:
        return ""
    decoded_bytes = base64.urlsafe_b64decode(data.encode("ASCII"))
    return decoded_bytes.decode("utf-8", errors="replace")

# --------------------

def _extract_body(payload: dict) -> str:
    """Emails can be structured many different ways. This func
    identifies the msg structure and pulls out the best text it can
    find, preferably plain text rather than html."""

    mime_type = payload.get("mimeType", "")

    # "Base Case"
    if mime_type == "text/plain" and "data" in payload.get("body", {}):
        return _decode_body_part(payload["body"]["data"])
    
    # HTML case
    if mime_type == "text/html" and "data" in payload.get("body", {}):
        return _strip_html(_decode_body_part(payload["body"]["data"]))
    
    # Multipart case, using recursion until we reach plain text.
    parts = payload.get("parts", [])
    plain_text, html_text = "", ""
    for part in parts:
        part_mime = part.get("mimeType", "")
        if part_mime == "text/plain" and "data" in part.get("body", {}):
            plain_text += _decode_body_part(part["body"]["data"])
        elif part_mime == "text/html" and "data" in part.get("body", {}):
            html_text += _strip_html(_decode_body_part(part["body"]["data"]))
        elif "parts" in part:
            # Nested multipart
            nested = _extract_body(part)
            if nested:
                plain_text += nested

    return plain_text.strip() or html_text.strip()

# --------------------

def _get_header(headers: list, name: str) -> str:
    """Gmail returns email headers (From, Subject, Date, ...) as a list
    of {"name": ..., "value": ...} dicts. This is a small helper to grab
    the one we want."""
 
    for header in headers:
        if header.get("name", "").lower() == name.lower():
            return header.get("value", "")
    return ""
 
 
def fetch_unread_emails(service, limit: int = 20) -> list[EmailMessage]:
    """Fetch up to `limit` unread emails from the inbox and return them
    as a list of EmailMessage objects, ready for classification."""
 
    response = (
        service.users()
        .messages()
        .list(userId="me", labelIds=["INBOX", "UNREAD"], maxResults=limit)
        .execute()
    )
    message_refs = response.get("messages", [])
 
    emails = []
    for ref in message_refs:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=ref["id"], format="full")
            .execute()
        )
        headers = msg["payload"].get("headers", [])
        body_text = _extract_body(msg["payload"])

        # Truncate very long emails so we don't send excessive text (and
        # cost) to the AI model
        MAX_BODY_CHARS = 4000
        if len(body_text) > MAX_BODY_CHARS:
            body_text = body_text[:MAX_BODY_CHARS] + " ... [truncated]"

        emails.append(
            EmailMessage(
                id=ref["id"],
                sender=_get_header(headers, "From"),
                subject=_get_header(headers, "Subject") or "(no subject)",
                date=_get_header(headers, "Date"),
                body=body_text,
            )
        )

    return emails
