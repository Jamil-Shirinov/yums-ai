"""
The Flask web app: accounts, plans, connecting Gmail, and a dashboard with
one big "Analyze my inbox" button.

The actual thinking is still done by the same two modules the CLI uses -
gmail_client.fetch_unread_emails() and classifier.classify_emails(). This
file wraps them in a login system and some HTML so nobody has to touch a
terminal.

Run it with:  python app.py
"""

import json
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from functools import wraps

from dotenv import load_dotenv
from flask import (
    Flask, Response, flash, redirect, render_template, request, session, url_for
)
from openai import OpenAI
from werkzeug.security import check_password_hash, generate_password_hash

import billing
import database
import encryption
import gmail_oauth
import mailer
from classifier import classify_emails
from gmail_client import fetch_unread_emails
from plans import DEFAULT_PLAN, PLANS, TIERS, get_plan

# How long a signup confirmation code stays usable, and how many wrong
# guesses we allow before making the user request a fresh one. Six digits is
# only a million possibilities, so without a cap they could all be tried.
CODE_TTL_MINUTES = 15
MAX_CODE_ATTEMPTS = 5

# Analyses run on background threads so the browser isn't left hanging for
# minutes on a big inbox. The pool is deliberately small: each analysis is a
# queue of OpenAI calls, and letting dozens run at once would just pile up
# requests. Extra analyses wait their turn rather than being refused.
ANALYSIS_WORKERS = 4

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

# Checked before anything touches the database, so a missing key is an
# obvious error the moment you start the app rather than a mystery the first
# time somebody connects their Gmail.
encryption.ensure_key()

database.init_db()

# Background threads don't survive a restart, so anything still flagged as
# running belongs to a process that's already gone.
interrupted = database.fail_interrupted_runs()
if interrupted:
    print(f"[app] marked {interrupted} interrupted analysis run(s) as failed")

analysis_pool = ThreadPoolExecutor(
    max_workers=ANALYSIS_WORKERS, thread_name_prefix="analysis"
)

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


def start_verification(user_id: int, email: str) -> bool:
    """Generate a confirmation code, save it, and email it to the user.

    Returns whether it actually went out by email - if not, the caller tells
    the user to look in the server console instead.
    """

    # secrets, not random: this is a credential, so it needs to be
    # unguessable rather than merely arbitrary. The formatting keeps leading
    # zeros, so every one of the million codes is equally likely.
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = datetime.now() + timedelta(minutes=CODE_TTL_MINUTES)

    database.save_verification_code(user_id, code, expires_at.isoformat(timespec="seconds"))
    return mailer.send_verification_code(email, code)


def start_checkout(user, plan_id: str):
    """Send the user off to Stripe to pay for a plan.

    Used from two places - finishing signup, and changing plan later - so it
    lives here rather than being written twice.
    """

    try:
        checkout_url = billing.create_checkout_session(
            user,
            plan_id,
            # Stripe swaps {CHECKOUT_SESSION_ID} for the real id when it
            # sends the customer back, so the success page knows what to
            # look up. It must not be URL-encoded, hence the plain join.
            success_url=url_for("billing_success", _external=True)
            + "?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=url_for("billing_cancel", _external=True),
        )
        return redirect(checkout_url)
    except Exception as error:
        print(f"[start_checkout] {type(error).__name__}: {error}")
        flash("Couldn't reach the payment page just now. Please try again.", "error")
        return redirect(url_for("dashboard"))


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

    return {
        "user": current_user(),
        "PLANS": PLANS,
        "TIERS": TIERS,
        "get_plan": get_plan,
        # So pages can say "continue to payment" rather than "save" when a
        # choice is actually going to ask for a card.
        "billing_enabled": billing.is_configured(),
        "is_purchasable": billing.is_purchasable,
    }


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
            #
            # A paid plan has to be paid for, so the account starts on Free
            # and we only remember what they picked. That way the plan column
            # always means "what this account actually has". With Stripe not
            # configured there's nothing to pay, and the chosen plan applies
            # straight away exactly as it used to.
            if billing.is_purchasable(plan):
                user_id = database.create_user(
                    email, generate_password_hash(password), DEFAULT_PLAN
                )
                database.set_pending_plan(user_id, plan)
            else:
                user_id = database.create_user(email, generate_password_hash(password), plan)

            # Not logged in yet: the account stays unconfirmed, and only
            # pending_user_id is set, until they prove they can read the
            # inbox we just emailed.
            session.clear()
            session["pending_user_id"] = user_id
            emailed = start_verification(user_id, email)

            if emailed:
                flash(f"We've emailed a 6-digit code to {email}.", "success")
            else:
                flash(
                    "Email isn't set up on this server, so your code was printed "
                    "to the terminal running the app.",
                    "info",
                )
            return redirect(url_for("verify"))

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
        elif not user["verified"]:
            # Right password, but they never confirmed the address. Send a
            # fresh code rather than leaving them stuck on an old one.
            session.clear()
            session["pending_user_id"] = user["id"]
            start_verification(user["id"], user["email"])
            flash("Please confirm your email first - we've sent you a new code.", "info")
            return redirect(url_for("verify"))
        else:
            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/verify", methods=["GET", "POST"])
def verify():
    """Check the 6-digit code emailed at signup.

    The account isn't usable until this passes, which is what proves the
    person signing up can actually read mail at that address.
    """

    pending_id = session.get("pending_user_id")
    if pending_id is None:
        flash("Start by creating an account.", "info")
        return redirect(url_for("signup"))

    user = database.get_user_by_id(pending_id)
    if user is None:
        session.clear()
        return redirect(url_for("signup"))

    if user["verified"]:
        # Already done - probably a stale tab. Just log them in.
        session.clear()
        session["user_id"] = user["id"]
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        entered = request.form.get("code", "").strip()

        if not user["verification_code"]:
            flash("That code is no longer valid. Send yourself a new one.", "error")
        elif user["verification_attempts"] >= MAX_CODE_ATTEMPTS:
            flash("Too many incorrect codes. Send yourself a new one.", "error")
        elif datetime.now() > datetime.fromisoformat(user["verification_expires_at"]):
            flash("That code has expired. Send yourself a new one.", "error")
        elif not secrets.compare_digest(entered, user["verification_code"]):
            database.record_failed_attempt(user["id"])
            remaining = MAX_CODE_ATTEMPTS - (user["verification_attempts"] + 1)
            if remaining > 0:
                flash(f"That code isn't right. {remaining} attempt(s) left.", "error")
            else:
                flash("Too many incorrect codes. Send yourself a new one.", "error")
        else:
            database.mark_verified(user["id"])
            session.clear()
            session["user_id"] = user["id"]
            flash("Email confirmed. Welcome to Yums!", "success")

            # If they signed up for a paid plan, this is the moment to
            # collect payment. Abandoning checkout just leaves them on Free
            # with a working account, which they can upgrade whenever.
            pending = database.get_user_by_id(user["id"])["pending_plan"]
            if pending and billing.is_purchasable(pending):
                return start_checkout(database.get_user_by_id(user["id"]), pending)

            return redirect(url_for("dashboard"))

    return render_template("verify.html", email=user["email"])


@app.route("/verify/resend", methods=["POST"])
def resend_code():
    """Throw away the pending code and email a fresh one."""

    pending_id = session.get("pending_user_id")
    if pending_id is None:
        return redirect(url_for("signup"))

    user = database.get_user_by_id(pending_id)
    if user is None or user["verified"]:
        session.clear()
        return redirect(url_for("login"))

    if start_verification(user["id"], user["email"]):
        flash(f"New code sent to {user['email']}.", "success")
    else:
        flash("Email isn't set up on this server - check the terminal for your code.", "info")

    return redirect(url_for("verify"))


@app.route("/account/delete", methods=["GET", "POST"])
@login_required
def delete_account():
    """First of two confirmations for deleting an account.

    This one just lays out what's about to happen. Agreeing here doesn't
    delete anything - it unlocks the second page, which is where the account
    actually goes.
    """

    user = current_user()

    if request.method == "POST":
        # Marks that they've read the warning. The second page checks for
        # this, so nobody lands there straight from a link.
        session["delete_account_confirmed"] = True
        return redirect(url_for("delete_account_confirm"))

    return render_template(
        "delete_account.html",
        plan=get_plan(user["plan"]),
        report_count=len(database.list_runs(user["id"], limit=1000)),
    )


@app.route("/account/delete/confirm", methods=["GET", "POST"])
@login_required
def delete_account_confirm():
    """Second confirmation, and the deletion itself.

    Asking for the email and the password isn't ceremony: it stops a
    logged-in machine left unattended being one click away from wiping
    somebody's account, and makes it hard to do by accident.
    """

    user = current_user()

    if not session.get("delete_account_confirmed"):
        return redirect(url_for("delete_account"))

    if request.method == "POST":
        typed_email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if typed_email != user["email"]:
            flash("That email doesn't match this account.", "error")
        elif not check_password_hash(user["password_hash"], password):
            flash("That password isn't right.", "error")
        else:
            # Stop an analysis mid-flight, so its thread isn't left running
            # up an OpenAI bill for an account that no longer exists.
            active_run_id = database.get_active_run(user["id"])
            if active_run_id:
                database.request_cancel(active_run_id, user["id"])

            # Cancel billing before the record goes, or Stripe would happily
            # keep charging a customer we can no longer see.
            if user["stripe_subscription_id"] and billing.is_configured():
                try:
                    billing.cancel_subscription(user["stripe_subscription_id"])
                except Exception as error:
                    print(f"[delete_account] couldn't cancel subscription: {error}")

            # Hand the Gmail grant back rather than just forgetting it.
            if user["gmail_token"]:
                try:
                    gmail_oauth.revoke_token(database.get_gmail_token(user["id"]))
                except Exception as error:
                    print(f"[delete_account] couldn't revoke Gmail access: {error}")

            database.delete_account(user["id"])
            session.clear()
            flash("Your account and all of its reports have been deleted.", "info")
            return redirect(url_for("index"))

    return render_template("delete_account_confirm.html")


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
        active_run_id=database.get_active_run(user["id"]),
    )


@app.route("/upgrade", methods=["GET", "POST"])
@login_required
def upgrade():
    """Change the account's plan.

    Plans are simulated, so this switch is immediate and free. Once real
    billing is wired in, the POST branch is where a successful payment would
    have to happen before database.update_plan() gets called.
    """

    user = current_user()

    if request.method == "POST":
        new_plan = request.form.get("plan", "")

        if new_plan not in PLANS:
            flash("Please choose one of the available plans.", "error")
        elif new_plan == user["plan"]:
            flash("That's already your current plan.", "info")
            return redirect(url_for("dashboard"))

        # Dropping to Free while paying means cancelling a subscription.
        # Stripe's portal handles that properly - proration, when access
        # actually ends - so we hand it over rather than guessing.
        elif new_plan == DEFAULT_PLAN and user["stripe_subscription_id"]:
            flash("Cancel your subscription here and you'll move to Free.", "info")
            return redirect(url_for("billing_portal"))

        elif billing.is_purchasable(new_plan):
            return start_checkout(user, new_plan)

        else:
            database.update_plan(user["id"], new_plan)
            plan = get_plan(new_plan)
            flash(
                f"You're now on the {plan['name']} plan - "
                f"{plan['email_limit']} emails per analysis.",
                "success",
            )
            return redirect(url_for("dashboard"))

    return render_template("upgrade.html", current_plan_id=user["plan"])

# --------------------
# Billing
# --------------------

def apply_paid_checkout(checkout_session) -> None:
    """Put an account onto the plan a completed checkout paid for.

    Written to be safe to run more than once, because it is: Stripe retries
    webhooks, and the success page does the same thing independently so the
    customer isn't left waiting on a delivery that might take a few seconds.
    """

    metadata = checkout_session.get("metadata") or {}
    user_id = checkout_session.get("client_reference_id") or metadata.get("user_id")
    plan_id = metadata.get("plan_id")

    if not user_id or plan_id not in PLANS:
        print(f"[billing] checkout with no usable user/plan: {user_id} / {plan_id}")
        return

    # "no_payment_required" covers 100%-off coupons and trials.
    if checkout_session.get("payment_status") not in ("paid", "no_payment_required"):
        return

    user = database.get_user_by_id(int(user_id))
    if user is None:
        return

    if checkout_session.get("customer"):
        database.save_stripe_customer(user["id"], checkout_session["customer"])

    database.activate_subscription(user["id"], plan_id, checkout_session.get("subscription"))
    print(f"[billing] account {user['id']} is now on {plan_id}")


def apply_subscription_change(subscription, ended: bool) -> None:
    """React to a subscription changing inside Stripe.

    Covers the cases our own UI never sees: a card that stops working, a
    cancellation from the billing portal, or a plan switched there.
    """

    user = None
    if subscription.get("customer"):
        user = database.get_user_by_stripe_customer(subscription["customer"])

    if user is None:
        metadata_user = (subscription.get("metadata") or {}).get("user_id")
        if metadata_user:
            user = database.get_user_by_id(int(metadata_user))

    if user is None:
        return

    if ended or subscription.get("status") not in ("active", "trialing"):
        database.end_subscription(user["id"])
        print(f"[billing] account {user['id']} dropped back to free")
        return

    # Still paying, but possibly for something else now.
    items = (subscription.get("items") or {}).get("data") or []
    price_id = (items[0].get("price") or {}).get("id") if items else None
    plan_id = billing.plan_id_for_price(price_id)

    if plan_id and plan_id != user["plan"]:
        database.activate_subscription(user["id"], plan_id, subscription.get("id"))
        print(f"[billing] account {user['id']} moved to {plan_id} from Stripe")


@app.route("/billing/success")
@login_required
def billing_success():
    """Where Stripe sends the customer after a successful payment.

    The webhook is what makes a payment official, but it can land a moment
    later - so this checks the session itself and applies the same change,
    rather than showing someone a dashboard that hasn't caught up yet.
    """

    session_id = request.args.get("session_id", "")

    if session_id:
        try:
            checkout_session = billing.get_checkout_session(session_id)
            # Only ever act on a session belonging to whoever is logged in.
            if str(checkout_session.get("client_reference_id")) == str(current_user()["id"]):
                apply_paid_checkout(checkout_session)
        except Exception as error:
            print(f"[billing_success] {type(error).__name__}: {error}")

    plan = get_plan(current_user()["plan"])
    flash(f"Payment received - you're on the {plan['name']} plan. Thanks!", "success")
    return redirect(url_for("dashboard"))


@app.route("/billing/cancel")
@login_required
def billing_cancel():
    """Where Stripe sends the customer if they back out of checkout."""

    flash("Checkout cancelled - nothing was charged and your plan is unchanged.", "info")
    return redirect(url_for("dashboard"))


@app.route("/billing/portal", methods=["GET", "POST"])
@login_required
def billing_portal():
    """Hand off to Stripe's billing portal to update a card or cancel."""

    user = current_user()

    if not user["stripe_customer_id"]:
        flash("There's no subscription on this account yet.", "info")
        return redirect(url_for("dashboard"))

    try:
        portal_url = billing.create_portal_session(
            user["stripe_customer_id"], url_for("dashboard", _external=True)
        )
        return redirect(portal_url)
    except Exception as error:
        print(f"[billing_portal] {type(error).__name__}: {error}")
        flash("Couldn't open the billing portal just now. Please try again.", "error")
        return redirect(url_for("dashboard"))


@app.route("/stripe/webhook", methods=["POST"])
def stripe_webhook():
    """Where Stripe tells us what actually happened.

    Deliberately outside the login system - Stripe has no session with us.
    The signature check is what makes it safe: without it, anyone who found
    this URL could hand out free subscriptions.
    """

    try:
        event = billing.verify_webhook(
            request.data, request.headers.get("Stripe-Signature", "")
        )
    except RuntimeError as error:
        # No signing secret configured - our problem, not Stripe's, so 500
        # tells it to retry once we've fixed it.
        print(f"[stripe_webhook] {error}")
        return "", 500
    except Exception as error:
        print(f"[stripe_webhook] rejected: {type(error).__name__}: {error}")
        return "", 400

    event_type = event["type"]
    payload = event["data"]["object"]

    if event_type == "checkout.session.completed":
        apply_paid_checkout(payload)
    elif event_type in ("customer.subscription.updated", "customer.subscription.deleted"):
        apply_subscription_change(payload, ended=event_type.endswith("deleted"))

    # Anything else we don't care about, but still acknowledge - an
    # unanswered webhook is one Stripe keeps retrying.
    return "", 200

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

def perform_analysis(user_id: int, run_id: int, limit: int) -> None:
    """Do the actual work, on a background thread.

    Nothing in here touches Flask - no session, no flash, no request - because
    none of that exists off the request thread. Progress and problems are
    written to the run row instead, and the results page reads them back.
    """

    # The Stop button can't kill this thread, so it raises a flag on the run
    # row instead and we check it between emails.
    def stop_requested() -> bool:
        return database.get_run_status(run_id) == "cancelling"

    try:
        service, refreshed_token = gmail_oauth.build_service(
            database.get_gmail_token(user_id)
        )
        if refreshed_token:
            database.save_gmail_token(user_id, refreshed_token)

        emails = fetch_unread_emails(service, limit=limit, should_stop=stop_requested)
        database.set_run_total(run_id, len(emails))

        # An empty inbox still finishes as a real (empty) report rather than
        # an error - "you're all caught up" is a legitimate answer.
        summaries = classify_emails(
            get_openai_client(),
            emails,
            on_progress=lambda done: database.record_run_progress(run_id, done),
            should_stop=stop_requested,
        )

        # Whatever was classified before stopping is still worth keeping.
        if stop_requested():
            database.cancel_run(run_id, summaries)
        else:
            database.complete_run(run_id, summaries)

    except RuntimeError as error:
        # Our own messages ("reconnect your Gmail", "no API key") are written
        # for a human to read, so they can be shown as-is.
        database.fail_run(run_id, str(error))
    except Exception as error:
        print(f"[perform_analysis] run {run_id}: {type(error).__name__}: {error}")
        database.fail_run(
            run_id, "Something went wrong while analyzing your inbox. Please try again."
        )


@app.route("/analyze", methods=["POST"])
@login_required
def analyze():
    """Start an analysis and send the user straight to its progress page.

    The work itself happens on a background thread, so this returns in
    milliseconds however big the inbox is. Redirecting (rather than rendering)
    also means refreshing the report never re-runs - or re-bills - anything.
    """

    user = current_user()

    if not user["gmail_token"]:
        flash("Connect your Gmail account first.", "error")
        return redirect(url_for("dashboard"))

    # One at a time per account, or an impatient second click would start a
    # second analysis and a second OpenAI bill.
    active_run_id = database.get_active_run(user["id"])
    if active_run_id:
        flash("An analysis is already running.", "info")
        return redirect(url_for("results", run_id=active_run_id))

    # Checked here rather than in the worker, so a missing key is an instant
    # error instead of a run that starts and immediately fails.
    try:
        get_openai_client()
    except RuntimeError as error:
        flash(str(error), "error")
        return redirect(url_for("dashboard"))

    run_id = database.create_pending_run(user["id"])
    analysis_pool.submit(
        perform_analysis, user["id"], run_id, get_plan(user["plan"])["email_limit"]
    )

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


@app.route("/results/<int:run_id>/stop", methods=["POST"])
@login_required
def stop_analysis(run_id: int):
    """Ask a running analysis to stop.

    It won't stop instantly: the worker finishes the email it's on, keeps
    everything classified so far, and closes the run off there.
    """

    user = current_user()

    if database.get_run(run_id, user["id"]) is None:
        flash("That analysis doesn't exist.", "error")
        return redirect(url_for("dashboard"))

    if database.request_cancel(run_id, user["id"]):
        flash("Stopping the analysis. Yums will keep everything that was done so far!", "info")
    else:
        flash("That analysis had already finished.", "info")

    return redirect(url_for("results", run_id=run_id))


@app.route("/results/<int:run_id>/delete", methods=["GET", "POST"])
@login_required
def delete_results(run_id: int):
    """Delete a report, asking first.

    GET shows the "are you sure?" page and POST does the deleting, so the
    confirmation can't be skipped and nothing is destroyed by following a
    link. Being a separate page rather than a JavaScript confirm() means the
    prompt still appears with scripting turned off.
    """

    user = current_user()
    run = database.get_run(run_id, user["id"])

    if run is None:
        flash("That report doesn't exist.", "error")
        return redirect(url_for("dashboard"))

    # Deleting the row out from under a working thread would leave it running
    # with nowhere to write - and free the account up to start another one.
    if run["status"] in ("running", "cancelling"):
        flash("Stop this analysis before deleting it.", "error")
        return redirect(url_for("results", run_id=run_id))

    if request.method == "POST":
        database.delete_run(run_id, user["id"])
        flash("Report deleted.", "info")
        return redirect(url_for("dashboard"))

    actions = [r for r in run["results"] if r["category"] == "action"]
    return render_template("confirm_delete.html", run=run, action_count=len(actions))


@app.route("/results/<int:run_id>/download")
@login_required
def download_results(run_id: int):
    """Hand the report back as a JSON file."""

    user = current_user()
    run = database.get_run(run_id, user["id"])

    if run is None:
        flash("That report doesn't exist.", "error")
        return redirect(url_for("dashboard"))

    if run["status"] in ("running", "cancelling"):
        flash("That analysis is still running - wait for it to finish first.", "info")
        return redirect(url_for("results", run_id=run_id))

    actions = [r for r in run["results"] if r["category"] == "action"]

    export = {
        "generated_by": "Yums AI",
        "run_id": run["id"],
        "created_at": run["created_at"],
        "status": run["status"],
        "counts": {
            "total": len(run["results"]),
            "actions": len(actions),
            "notices": len(run["results"]) - len(actions),
        },
        "results": run["results"],
    }

    # Named after when the analysis ran, not when it was downloaded, so two
    # downloads of the same report give the same file.
    stamp = datetime.fromisoformat(run["created_at"])
    filename = f"analysis-{stamp.strftime('%Y-%m-%d')}-{stamp.strftime('%H%M%S')}.json"

    return Response(
        json.dumps(export, indent=2),
        mimetype="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

# --------------------

if __name__ == "__main__":
    app.run(debug=True, port=5000)
