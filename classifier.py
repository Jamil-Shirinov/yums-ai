"""
Takes one EmailMessage and asks OpenAI's API to do 2 things:
1. Classify it as an "action" or a "notice"
2. Write a one-liner explanation
"""

import json
from openai import OpenAI
from models import EmailMessage, EmailSummary

MODEL_NAME = "gpt-4o-mini"

SYSTEM_PROMPT = """You are an inbox triage assistant for a busy professional.
For each email you are shown, decide whether it belongs in one of two buckets:
 
- "action": the recipient needs to DO something - reply, approve, sign,
  complete a task, make a decision, fix an issue, or respond by a deadline.
- "notice": purely informational - newsletters, automated confirmations,
  FYI updates, receipts, notifications the recipient doesn't need to act on.
 
When in doubt between the two, prefer "action" - it's safer to flag
something as needing attention than to let it get missed.
 
Respond ONLY with a JSON object in exactly this shape, no extra text:
{
  "category": "action" or "notice",
  "summary": "one concise sentence describing what the email is about",
  "reason": "one short phrase explaining why you chose that category"
}
"""

# --------------------

def classify_email(client: OpenAI, email: EmailMessage) -> EmailSummary:
    """Send one email to OpenAI and turn the response into an EmailSummary. If
    anything goes wrong, we just fail safe by defaulting to "notice" rather
    than just dropping the email."""

    user_message = (
        f"From: {email.sender}\n"
        f"Subject: {email.subject}\n"
        f"Date: {email.date}\n\n"
        f"Body:\n{email.body}"
    )

    response = client.chat.completions.create(
        model=MODEL_NAME,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        temperature=0.000,
    )

    raw_content = response.choices[0].message.content

    try:
        parsed = json.loads(raw_content)
        category = parsed.get("category", "notice").lower().strip()
        if category not in ("action", "notice"):
            category = "notice"
        summary = parsed.get("summary", "(no summary provided)")
        reason = parsed.get("reason", "")
    except (json.JSONDecodeError, AttributeError):
        # The model did not return valid JSON. Rather than crash, log it as notice with included note.
        category = "notice"
        summary = "Could not automatically summarize this email."
        reason = "Model response could not be parsed."

    return EmailSummary(email=email, category=category, summary=summary, reason=reason)

# --------------------

def classify_emails(client: OpenAI, emails: list[EmailMessage]) -> list[EmailSummary]:
    """Classify a whole batch of emails, one at a time."""
    return [classify_email(client, email) for email in emails]
