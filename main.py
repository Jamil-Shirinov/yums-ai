"""
The main file.
"""
 
import argparse
import os
from datetime import datetime
from dotenv import load_dotenv
from openai import OpenAI
from classifier import classify_emails
from gmail_client import fetch_unread_emails, get_gmail_service
from models import EmailSummary

# --------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize unread Gmail emails into Actions and Notices."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Max number of unread emails to process (default: 20). "
        "Keeps this from processing the user's entire inbox and driving us broke because of the accidental, immense token usage",
    )
    parser.add_argument(
        "--mark-as-read",
        action="store_true",
        help="If set: marks processed emails as read in Gmail after summarizing." "",
    )
    return parser.parse_args()

# --------------------

def print_report(summaries: list[EmailSummary]) -> str:
    """Print a report to the terminal, and return the same content as a Markdown string so we can also save it to a file."""

    actions = [s for s in summaries if s.category == "action"]
    notices = [s for s in summaries if s.category == "notice"]

    lines = []
    lines.append(f"# Inbox Summary - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    lines.append(f"**{len(summaries)}** unread emails processed "
                  f"({len(actions)} actions, {len(notices)} notices)")
    lines.append("")

    lines.append("* Actions Needed")
    lines.append("")
    if actions:
        for i, s in enumerate(actions, start=1):
            lines.append(f"{i}. **{s.email.subject}** — from {s.email.sender}")
            lines.append(f"   - {s.summary}")
    else:
        lines.append("_Nothing needs your attention right now._")
    lines.append("")

    lines.append("* Notices")
    lines.append("")
    if notices:
        for i, s in enumerate(notices, start=1):
            lines.append(f"{i}. **{s.email.subject}** — from {s.email.sender}")
            lines.append(f"   ——— {s.summary}")
    else:
        lines.append("_No notices._")
    lines.append("")

    report = "\n".join(lines)
    print(report)
    return report

# --------------------

def save_report(report_markdown: str) -> str:
    """Save the report to a timestamped file in the output/ folder and
    return the path it was saved to :p"""

    os.makedirs("output", exist_ok=True)
    filename = f"output/inbox_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(report_markdown)
    return filename

# --------------------
 
def main():
    args = parse_args()

    # Load the OpenAI key
    load_dotenv()
    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key:
        raise SystemExit(
            "No OPENAI_API_KEY found. Copy .env.example to .env and add your key - "
            "see the README for details."
        )
    openai_client = OpenAI(api_key=openai_api_key)

    print("Logging in to Gmail...")
    gmail_service = get_gmail_service()

    print(f"Fetching up to {args.limit} unread emails...")
    emails = fetch_unread_emails(gmail_service, limit=args.limit)

    if not emails:
        print("No unread emails found. You're all caught up!")
        return

    print(f"Found {len(emails)} unread email(s). Analyzing...")
    summaries = classify_emails(openai_client, emails)

    report = print_report(summaries)
    saved_path = save_report(report)
    print(f"\nSaved report to {saved_path}")

    if args.mark_as_read:
        print(
            "\n--mark-as-read was requested, but this project only requests "
            "read-only Gmail access by design. See the README for how to "
            "opt in to the broader scope if you want this feature."
        )
 
if __name__ == "__main__":
    main()
    