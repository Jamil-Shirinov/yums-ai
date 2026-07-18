"""
A quick way to test the classifier without having to set up Gmail OAuth.
Feeds several fake emails straight into classifier.py.
"""

import os
from dotenv import load_dotenv
from openai import OpenAI
from classifier import classify_email
from models import EmailMessage

SAMPLE_EMAILS = [
    #f-something stands for fake emails
    EmailMessage(
        id="f-1",
        sender="Herring Herringson <herring@example.com>",
        subject="Action required: sign your updated benefits form by Friday",
        date="Fri, 03 Jul 2026 09:00:00",
        body=(
            "Hi Jamil, please review and sign the attached benefits enrollment form by end of day Friday. Let me know if you have questions."
        ),
    ),

    EmailMessage(
        id="f-2",
        sender="ABoringNewsletter <news@boring.com>",
        subject="This week: 5 stories you might have missed",
        date="Fri, 03 Jul 2026 07:00:00",
        body=(
            "Here's your weekly roundup of tech news. Just some interesting reads for your morning coffee."
        ),
    ),

    EmailMessage(
        id="f-3",
        sender="GitHub <notifications@github.com>",
        subject="[fake-repo] Pull request #67: Fix login bug",
        date="Fri, 03 Jul 2026 07:30:00",
        body=(
            "Salmon Salmonson opened a pull request that needs your review before it can be merged: 'Fix login bug'. Please review when you have a chance."
        ),
    ),
]

NUM_OF_SAMPLE_EMAILS = len(SAMPLE_EMAILS)

# --------------------

def main():
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit(
            "No OPENAI_API_KEY found."
        )

    client = OpenAI(api_key=api_key)

    print(f"Running Yums on {NUM_OF_SAMPLE_EMAILS} sample emails...\n")
    for email in SAMPLE_EMAILS:
        result = classify_email(client, email)
        print(f"Subject: {result.email.subject}")
        print(f" -> Category: {result.category.upper()}")
        print(f" -> Summary:  {result.summary}")
        print(f" -> Reason:   {result.reason}")
        print()

    print(
        "Expected: emails 1 and 3 should be classified as ACTION, email 2 should be classified as NOTICE."
    )

if __name__ == "__main__":
    main()