"""
The Flask web app: accounts, plans, connecting Gmail, and a dashboard with
one big "Analyze my inbox" button.

The actual thinking is still done by the same two modules the CLI uses -
gmail_client.fetch_unread_emails() and classifier.classify_emails(). This
file wraps them in a login system and some HTML so nobody has to touch a
terminal.

Run it with:  python app.py
"""

import os
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, session, url_for
from openai import OpenAI
from werkzeug.security import check_password_hash, generate_password_hash

import database
import gmail_oauth
from classifier import classify_emails
from gmail_client import fetch_unread_emails
from plans import DEFAULT_PLAN, PLANS, get_plan

load_dotenv()

app = Flask(__name__)

# Flask signs the session cookie with this. In production it must come from
# .env - if it changes, everybody gets logged out.
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-only-secret-change-me")

# Browsers already default to Lax, but setting it explicitly means the
# protection is on purpose: another site can't make a logged-in user's browser
# POST to /analyze, because the session cookie won't be sent cross-site.
# (Lax still allows the top-level redirect back from Google's consent screen.)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True

database.init_db()

# --------------------
# Small helpers
# --------------------

def current_user():
    """Return the logged-in user's row, or None if nobody is logged in."""

    user_id = session.get("user_id")
    if user_id is None:
        return None
    return database.get_user_by_id(user_id)


def login_required(view):
    """Decorator for pages that need an account. Bounces guests to /login."""

    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if current_user() is None:
            flash("Please log in to continue.", "error")
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped_view


def get_openai_client() -> OpenAI:
    """Build the OpenAI client. One key for the whole platform - customers
    pay us for a plan, we pay OpenAI."""

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "No OPENAI_API_KEY found on the server. Copy .env.example to .env "
            "and add your key."
        )
    return OpenAI(api_key=api_key)


@app.context_processor
def inject_globals():
    """Make these available inside every template without passing them in
    to each render_template() call by hand."""

    return {"user": current_user(), "PLANS": PLANS, "get_plan": get_plan}


@app.template_filter("sender_name")
def sender_name(sender: str) -> str:
    """Gmail gives us senders like 'Jamil Jamilson <json@example.com>'. Show
    the human-readable name when there is one, otherwise the bare address."""

    if "<" in sender:
        name = sender.split("<")[0].strip().strip('"')
        if name:
            return name
        return sender.split("<")[1].rstrip(">").strip()
    return sender.strip()

# --------------------
# Public pages
# --------------------

@app.route("/")
def index():
    """Landing page + pricing. Straight to the dashboard if already logged in."""

    if current_user() is not None:
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    """Create an account and pick a plan."""

    # Lets the pricing page link straight to a pre-selected plan.
    selected_plan = request.args.get("plan", DEFAULT_PLAN)
    email = ""

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        plan = request.form.get("plan", DEFAULT_PLAN)
        selected_plan = plan

        # Validation, cheapest checks first.
        if "@" not in email or "." not in email:
            flash("That doesn't look like a valid email address.", "error")
        elif len(password) < 8:
            flash("Please use a password of at least 8 characters.", "error")
        elif plan not in PLANS:
            flash("Please choose one of the available plans.", "error")
        elif database.get_user_by_email(email) is not None:
            flash("There's already an account with that email. Try logging in.", "error")
        else:
            # generate_password_hash salts and hashes for us - the plain
            # password is never written down anywhere.
            user_id = database.create_user(email, generate_password_hash(password), plan)
            session["user_id"] = user_id
            flash(f"Welcome to Yums! You're on the {get_plan(plan)['name']} plan.", "success")
            return redirect(url_for("dashboard"))

    # On a failed POST we hand the email back so the user doesn't have to
    # retype it along with their password.
    return render_template("signup.html", selected_plan=selected_plan, email=email)


@app.route("/login", methods=["GET", "POST"])
def login():
    """Log in with the email and password used at signup."""

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = database.get_user_by_email(email)

        # Deliberately one vague message for both "no such account" and "wrong
        # password", so this page can't be used to find out who has an account.
        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Incorrect email or password.", "error")
        else:
            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You've been logged out.", "info")
    return redirect(url_for("index"))

# --------------------
# The dashboard
# --------------------

@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    return render_template(
        "dashboard.html",
        plan=get_plan(user["plan"]),
        history=database.list_runs(user["id"]),
    )

# --------------------
# Connecting Gmail
# --------------------

@app.route("/gmail/connect")
@login_required
def gmail_connect():
    """Step 1: send the user over to Google to approve read-only access."""

    try:
        flow = gmail_oauth.build_flow(url_for("gmail_callback", _external=True))
    except RuntimeError as error:
        flash(str(error), "error")
        return redirect(url_for("dashboard"))

    auth_url, state = flow.authorization_url(
        access_type="offline",      # so we get a refresh token, not just a 1-hour one
        include_granted_scopes="true",
        prompt="consent",           # makes sure a refresh token comes back every time
    )

    # Remember the state so we can check Google sends the same one back.
    session["oauth_state"] = state
    return redirect(auth_url)


@app.route("/gmail/callback")
@login_required
def gmail_callback():
    """Step 2: Google sends the user back here. Swap the code for a token."""

    user = current_user()
    expected_state = session.pop("oauth_state", None)

    # If these don't match, this request didn't start from our connect button,
    # so we don't trust it.
    if not expected_state or request.args.get("state") != expected_state:
        flash("That Gmail connection attempt expired. Please try again.", "error")
        return redirect(url_for("dashboard"))

    if request.args.get("error"):
        flash("Gmail access was declined, so there's nothing to analyze yet.", "info")
        return redirect(url_for("dashboard"))

    try:
        flow = gmail_oauth.build_flow(url_for("gmail_callback", _external=True))
        flow.fetch_token(authorization_response=request.url)
        credentials = flow.credentials

        # Ask Gmail which address we just connected to, so we can show it.
        service, _ = gmail_oauth.build_service(credentials.to_json())
        address = gmail_oauth.get_connected_address(service)

        database.save_gmail_token(user["id"], credentials.to_json(), address)
        flash(f"Gmail connected: {address}", "success")
    except Exception as error:
        print(f"[gmail_callback] {type(error).__name__}: {error}")
        flash("Couldn't finish connecting to Gmail. Please try again.", "error")

    return redirect(url_for("dashboard"))


@app.route("/gmail/disconnect", methods=["POST"])
@login_required
def gmail_disconnect():
    """Forget the stored token. The account itself stays put."""

    database.clear_gmail_token(current_user()["id"])
    flash("Gmail disconnected. We've deleted the stored access token.", "info")
    return redirect(url_for("dashboard"))

# --------------------
# The actual analysis
# --------------------

@app.route("/analyze", methods=["POST"])
@login_required
def analyze():
    """The one button on the dashboard: fetch unread mail, classify it, save
    the results, then redirect to the report.

    Redirecting afterwards (rather than rendering here) means refreshing the
    report page doesn't quietly run - and bill for - a second analysis.
    """

    user = current_user()

    if not user["gmail_token"]:
        flash("Connect your Gmail account first.", "error")
        return redirect(url_for("dashboard"))

    limit = get_plan(user["plan"])["email_limit"]

    try:
        service, refreshed_token = gmail_oauth.build_service(user["gmail_token"])
        if refreshed_token:
            database.save_gmail_token(user["id"], refreshed_token)

        emails = fetch_unread_emails(service, limit=limit)

        if not emails:
            flash("No unread emails found. You're all caught up!", "info")
            return redirect(url_for("dashboard"))

        summaries = classify_emails(get_openai_client(), emails)
        run_id = database.create_run(user["id"], summaries)

    except RuntimeError as error:
        # Our own "reconnect your Gmail" / "no API key" messages are safe to show.
        flash(str(error), "error")
        return redirect(url_for("dashboard"))
    except Exception as error:
        print(f"[analyze] {type(error).__name__}: {error}")
        flash("Something went wrong while analyzing your inbox. Please try again.", "error")
        return redirect(url_for("dashboard"))

    return redirect(url_for("results", run_id=run_id))


@app.route("/results/<int:run_id>")
@login_required
def results(run_id: int):
    """Show one saved report."""

    run = database.get_run(run_id, current_user()["id"])
    if run is None:
        flash("That report doesn't exist.", "error")
        return redirect(url_for("dashboard"))

    return render_template(
        "results.html",
        run=run,
        actions=[r for r in run["results"] if r["category"] == "action"],
        notices=[r for r in run["results"] if r["category"] != "action"],
    )

# --------------------

if __name__ == "__main__":
    app.run(debug=True, port=5000)
