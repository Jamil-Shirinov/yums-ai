"""
The little bot that emails signup confirmation codes.

It uses smtplib from the standard library, so there's no extra package to
install - just SMTP settings in .env. Any provider works: Gmail (with an App
Password), SendGrid, Mailgun, your school's SMTP server, whatever.

If SMTP isn't configured, sending falls back to printing the code in the
terminal so the signup flow can still be tried out locally. That fallback is
for development only - see the README, because in production it would mean
anyone reading the logs can walk into a new account.
"""

import os
import smtplib
import ssl
from email.message import EmailMessage

# --------------------

def _smtp_settings():
    """Pull the SMTP config out of the environment.

    Returns None if the essentials are missing, which is the caller's cue to
    fall back to printing the code.
    """

    host = os.getenv("SMTP_HOST")
    username = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")

    if not host or not username or not password:
        return None

    return {
        "host": host,
        "port": int(os.getenv("SMTP_PORT", "587")),
        "username": username,
        "password": password,
        # Most providers insist the From address matches the account sending it.
        "sender": os.getenv("SMTP_FROM", username),
    }

# --------------------

def _build_message(to_address: str, code: str, sender: str) -> EmailMessage:
    """Write the actual email."""

    message = EmailMessage()
    message["Subject"] = f"{code} is your Yums confirmation code"
    message["From"] = sender
    message["To"] = to_address
    message.set_content(
        f"Welcome to Yums!\n\n"
        f"Your confirmation code is: {code}\n\n"
        f"Enter it on the confirmation page to finish setting up your account.\n"
        f"The code expires in 15 minutes.\n\n"
        f"If you didn't sign up for Yums, you can ignore this email.\n"
    )
    return message

# --------------------

def send_verification_code(to_address: str, code: str) -> bool:
    """Email a confirmation code.

    Returns True if it really went out over SMTP, and False if we fell back
    to printing it to the console (either because SMTP isn't configured or
    because the send failed). The caller uses that to tell the user where to
    look for their code.
    """

    settings = _smtp_settings()

    if settings is None:
        print(f"\n[mailer] SMTP is not configured. Confirmation code for {to_address}: {code}\n")
        return False

    try:
        message = _build_message(to_address, code, settings["sender"])
        context = ssl.create_default_context()

        # Port 465 is TLS from the first byte; everything else starts plain
        # and upgrades with STARTTLS.
        if settings["port"] == 465:
            with smtplib.SMTP_SSL(settings["host"], settings["port"], context=context) as server:
                server.login(settings["username"], settings["password"])
                server.send_message(message)
        else:
            with smtplib.SMTP(settings["host"], settings["port"], timeout=20) as server:
                server.starttls(context=context)
                server.login(settings["username"], settings["password"])
                server.send_message(message)

        return True

    except Exception as error:
        # A broken mail server shouldn't lose somebody's signup, so log what
        # happened, print the code, and let them carry on.
        print(f"[mailer] couldn't send to {to_address}: {type(error).__name__}: {error}")
        print(f"[mailer] confirmation code for {to_address}: {code}")
        return False
