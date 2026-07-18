"""
dataclasses to pass information between gmail_client.py, classifier.py, and main.py.
"""

from dataclasses import dataclass

@dataclass
class EmailMessage:
    """A single unread email, after we've pulled the useful bits out of
    Gmail's raw API response."""
 
    id: str         # msg ID
    sender: str     # e.g. "Jamil Jamilson <json@ilikecapybaras.com>"
    subject: str    # msg subject
    date: str       # date string. e.g. "Mon, 15 Dec 2007 00:00:00"
    body: str       # msg plain-text body

@dataclass
class EmailSummary:
    """The result from classifying AND summarizing 1 EmailMessage."""

    email: EmailMessage
    category: str       # "action" or "notice"
    summary: str        # one-liner summary
    reason: str         # reason why it was categorized like this
    