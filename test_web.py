"""
A quick way to test the web platform without connecting Gmail or spending
anything on OpenAI.

    python test_web.py           # run the checks against a throwaway database
    python test_web.py --demo    # create a demo account you can log into

Everything here goes through Flask's test client, so it exercises the real
routes, the real templates, and the real database queries - it just never
talks to Google or OpenAI.
"""

import argparse
import os
import sys
import tempfile

from models import EmailMessage, EmailSummary

# --------------------

# A couple of pretend results, so we can see a report render without paying
# for a real classification run.
SAMPLE_RESULTS = [
    EmailSummary(
        email=EmailMessage(
            id="f-1",
            sender="Herring Herringson <herring@example.com>",
            subject="Action required: sign your benefits form by Friday",
            date="Fri, 03 Jul 2026 09:00:00",
            body="Please review and sign the attached form by end of day Friday.",
        ),
        category="action",
        summary="A benefits enrollment form needs your signature by Friday.",
        reason="You need to sign and return this before Friday.",
    ),
    EmailSummary(
        email=EmailMessage(
            id="f-2",
            sender="GitHub <notifications@github.com>",
            subject="[fake-repo] Pull request #67: Fix login bug",
            date="Fri, 03 Jul 2026 07:30:00",
            body="Salmon Salmonson opened a pull request that needs your review.",
        ),
        category="action",
        summary="A pull request is waiting on your review before it can merge.",
        reason="You need to review this before it can be merged.",
    ),
    EmailSummary(
        email=EmailMessage(
            id="f-3",
            # No display name on this one, so it also checks that the report
            # falls back to showing the bare address.
            sender="news@boring.com",
            subject="This week: 5 stories you might have missed",
            date="Fri, 03 Jul 2026 07:00:00",
            body="Here's your weekly roundup of tech news.",
        ),
        category="notice",
        summary="Weekly tech newsletter roundup.",
        reason="You don't need to do anything with this one.",
    ),
]

# --------------------

class Checker:
    """Keeps a tally so one failure doesn't stop the rest of the run."""

    def __init__(self):
        self.failures = []

    def check(self, label, condition, detail=""):
        if condition:
            print(f"  PASS  {label}")
        else:
            print(f"  FAIL  {label} {detail}")
            self.failures.append(label)

# --------------------

def run_checks() -> int:
    """Walk through the whole app the way a user would, and check what comes
    back. Returns the number of failures."""

    # Point the app at a temporary database BEFORE importing it, so the real
    # yums.db is left completely alone.
    temp_db = os.path.join(tempfile.mkdtemp(), "test_yums.db")
    os.environ["YUMS_DB_FILE"] = temp_db
    os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")

    import app as webapp
    import database

    checker = Checker()
    check = checker.check
    client = webapp.app.test_client()

    print("\n-- public pages --")
    response = client.get("/")
    check("landing page loads", response.status_code == 200, response.status_code)
    check("landing page lists plans", b"Business" in response.data and b"Free" in response.data)
    check("monthly prices shown", b"$2.99" in response.data and b"$7.99" in response.data)
    check("annual prices shown", b"$29.99" in response.data and b"$85.99" in response.data)
    check("annual savings shown", b"Save $6" in response.data and b"Save $9" in response.data)
    check("footer credit links to jshirinov.com",
          b"Created by" in response.data and b"jshirinov.com" in response.data)
    check("signup page loads", client.get("/signup").status_code == 200)
    check("annual plans selectable at signup",
          b'value="pro_annual"' in client.get("/signup").data)
    check("login page loads", client.get("/login").status_code == 200)

    print("\n-- pages that need an account --")
    response = client.get("/dashboard")
    check("guests bounced from dashboard",
          response.status_code == 302 and "/login" in response.headers["Location"])
    response = client.post("/analyze")
    check("guests bounced from analyze",
          response.status_code == 302 and "/login" in response.headers["Location"])

    print("\n-- signup rejects bad input --")
    response = client.post("/signup", data={"email": "notanemail", "password": "longenough1", "plan": "free"})
    check("malformed email rejected", b"valid email address" in response.data)
    response = client.post("/signup", data={"email": "a@b.com", "password": "short", "plan": "free"})
    check("short password rejected", b"at least 8 characters" in response.data)
    response = client.post("/signup", data={"email": "a@b.com", "password": "longenough1", "plan": "gold"})
    check("made-up plan rejected", b"available plans" in response.data)
    check("none of those created an account", database.get_user_by_email("a@b.com") is None)

    print("\n-- signup works --")
    response = client.post("/signup",
                           data={"email": "Boss@Company.com", "password": "correcthorse", "plan": "pro"})
    check("signup lands on dashboard",
          response.status_code == 302 and "/dashboard" in response.headers["Location"])
    user = database.get_user_by_email("boss@company.com")
    check("email saved in lowercase", user is not None)
    check("chosen plan saved", user and user["plan"] == "pro", user["plan"] if user else None)
    check("password stored hashed, not in plaintext",
          user and "correcthorse" not in user["password_hash"])
    check("gmail starts disconnected", user and user["gmail_token"] is None)
    response = client.post("/signup",
                           data={"email": "boss@company.com", "password": "another1234", "plan": "free"})
    check("same email can't sign up twice", b"already an account" in response.data)

    print("\n-- dashboard before connecting gmail --")
    response = client.get("/dashboard")
    check("dashboard loads", response.status_code == 200, response.status_code)
    check("asks the user to connect gmail", b"Connect Gmail" in response.data)
    check("shows the Pro limit of 25", b"25 unread emails" in response.data)
    check("plan shown by name only, without a price", b"$2.99" not in response.data)
    response = client.post("/analyze", follow_redirects=True)
    check("analyze refuses without gmail", b"Connect your Gmail account first" in response.data)

    print("\n-- reports --")
    run_id = database.create_run(user["id"], SAMPLE_RESULTS)
    response = client.get(f"/results/{run_id}")
    check("report loads", response.status_code == 200, response.status_code)
    check("action email shown", b"sign your benefits form" in response.data)
    check("notice email shown", b"5 stories you might have missed" in response.data)
    check("sender shown as a name", b"Herring Herringson" in response.data)
    check("sender falls back to address", b"news@boring.com" in response.data)
    check("explanation shown without a 'Why:' label",
          b"You need to sign and return this" in response.data and b"Why:" not in response.data)
    stored = database.get_run(run_id, user["id"])
    check("email bodies are NOT kept in the database",
          "body" not in stored["results"][0]["email"])
    response = client.get("/dashboard")
    check("report appears in history", b"3 emails" in response.data and b"2 actions" in response.data)
    check("nonexistent report redirects", client.get("/results/9999").status_code == 302)

    print("\n-- one account can't read another's report --")
    other = webapp.app.test_client()
    other.post("/signup", data={"email": "other@company.com", "password": "hunter2hunter2", "plan": "free"})
    response = other.get(f"/results/{run_id}", follow_redirects=True)
    check("other account can't see the report", b"sign your benefits form" not in response.data)

    print("\n-- login and logout --")
    check("logout redirects", client.get("/logout").status_code == 302)
    check("logged-out user bounced from dashboard", client.get("/dashboard").status_code == 302)
    response = client.post("/login", data={"email": "boss@company.com", "password": "wrongpassword"})
    check("wrong password refused", b"Incorrect email or password" in response.data)
    response = client.post("/login", data={"email": "ghost@nowhere.com", "password": "wrongpassword"})
    check("unknown account gets the same message (no account fishing)",
          b"Incorrect email or password" in response.data)
    response = client.post("/login", data={"email": "boss@company.com", "password": "correcthorse"})
    check("correct password logs in",
          response.status_code == 302 and "/dashboard" in response.headers["Location"])

    print("\n-- gmail connect without google credentials configured --")
    for variable in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"):
        os.environ.pop(variable, None)
    response = client.get("/gmail/connect", follow_redirects=True)
    check("missing google credentials explained clearly", b"GOOGLE_CLIENT_ID" in response.data)
    response = client.get("/gmail/callback?state=forged&code=abc", follow_redirects=True)
    check("forged oauth callback rejected", b"expired" in response.data)

    return len(checker.failures)

# --------------------

def seed_demo_account() -> None:
    """Put a demo account and a sample report into the real database, so the
    dashboard and report pages can be looked at in a browser without
    connecting Gmail or paying for a classification run."""

    import database
    from werkzeug.security import generate_password_hash

    # app.py normally does this on startup. We're running without it, so if
    # the server has never been started there'd be no tables yet.
    database.init_db()

    email = "demo@yums.ai"
    password = "demopassword"

    user = database.get_user_by_email(email)
    if user is None:
        user_id = database.create_user(email, generate_password_hash(password), "business")
        print(f"Created demo account: {email}")
    else:
        user_id = user["id"]
        print(f"Demo account already existed: {email}")

    database.create_run(user_id, SAMPLE_RESULTS)
    print("Added a sample report.\n")
    print("Now start the server with 'python app.py' and log in at")
    print("http://localhost:5000/login with:")
    print(f"    email:    {email}")
    print(f"    password: {password}")
    print("\nThe 'Analyze my inbox' button still needs a real Gmail connection,")
    print("but the sample report is visible under 'Recent analyses'.")

# --------------------

def main():
    parser = argparse.ArgumentParser(description="Test the Yums web platform.")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Instead of running the checks, add a demo account and a sample "
             "report to yums.db so the UI can be viewed in a browser.",
    )
    args = parser.parse_args()

    if args.demo:
        seed_demo_account()
        return

    failures = run_checks()
    print()
    if failures:
        print(f"{failures} check(s) FAILED.")
        sys.exit(1)
    print("All checks passed.")

if __name__ == "__main__":
    main()
