"""
Taking money, via Stripe.

Everything that talks to Stripe lives here, so the rest of the app only ever
deals in plan names. Two ideas run through it:

1. **Card details never touch this server.** We send the customer to a
   checkout page hosted by Stripe and they come back afterwards. That keeps
   us well clear of handling card numbers ourselves.

2. **A payment is only real once Stripe says so**, on a webhook. The browser
   coming back to our success page is a hint, not proof - somebody can close
   the tab, and a URL can be visited by hand. The account's plan is what they
   have paid for, and only Stripe events move it.

If Stripe isn't configured, is_configured() is False and the app falls back
to switching plans without payment - exactly how it worked before. That keeps
the whole thing runnable without a Stripe account.
"""

import os

import stripe

from plans import PLANS, STRIPE_PRICE_VARIABLES

# --------------------

def is_configured() -> bool:
    """Whether we have a secret key to talk to Stripe with."""

    return bool(os.getenv("STRIPE_SECRET_KEY"))


def _connect() -> None:
    """Point the Stripe library at our account.

    Read from the environment on each call rather than once at import, so
    tests can swap keys and .env changes take effect on restart.
    """

    stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

# --------------------

def price_id_for(plan_id: str):
    """The Stripe price id for a plan, or None if there isn't one."""

    variable = STRIPE_PRICE_VARIABLES.get(plan_id)
    return os.getenv(variable) if variable else None


def plan_id_for_price(price_id: str):
    """The reverse: work out which plan a Stripe price belongs to.

    Needed when a subscription changes in Stripe rather than in our app - a
    customer switching plan in the billing portal, for instance.
    """

    for plan_id in STRIPE_PRICE_VARIABLES:
        if price_id and price_id_for(plan_id) == price_id:
            return plan_id
    return None


def is_purchasable(plan_id: str) -> bool:
    """Whether this plan can actually be bought right now.

    False for Free (nothing to pay), and false for any paid plan whose price
    id hasn't been set up yet - in which case the app just switches to it
    without charging, as it did before.
    """

    return plan_id in PLANS and is_configured() and bool(price_id_for(plan_id))

# --------------------

def create_checkout_session(user, plan_id: str, success_url: str, cancel_url: str) -> str:
    """Start a subscription checkout and return the URL to send the user to.

    client_reference_id and the metadata are how the webhook works out who
    paid and for what - by the time Stripe calls us back there's no browser
    session to consult.
    """

    _connect()

    details = {
        "mode": "subscription",
        "line_items": [{"price": price_id_for(plan_id), "quantity": 1}],
        "success_url": success_url,
        "cancel_url": cancel_url,
        "client_reference_id": str(user["id"]),
        "metadata": {"user_id": str(user["id"]), "plan_id": plan_id},
        # Copied onto the subscription as well, so later events about it
        # (cancellations, plan changes) can still be traced back to a user.
        "subscription_data": {
            "metadata": {"user_id": str(user["id"]), "plan_id": plan_id}
        },
    }

    # Reuse the Stripe customer if this account has bought something before,
    # so their history stays on one customer record. Stripe rejects being
    # given both a customer and an email, so it's one or the other.
    if user["stripe_customer_id"]:
        details["customer"] = user["stripe_customer_id"]
    else:
        details["customer_email"] = user["email"]

    return stripe.checkout.Session.create(**details).url


def create_portal_session(customer_id: str, return_url: str) -> str:
    """Open Stripe's billing portal, where customers update their card or
    cancel. Building all that ourselves would be a lot of work for no gain."""

    _connect()
    return stripe.billing_portal.Session.create(
        customer=customer_id, return_url=return_url
    ).url


def cancel_subscription(subscription_id: str) -> None:
    """Stop billing a subscription immediately.

    Needed when somebody deletes their account: forgetting them on our side
    wouldn't stop Stripe charging their card every month.
    """

    _connect()
    stripe.Subscription.cancel(subscription_id)


def get_checkout_session(session_id: str):
    """Look a checkout session up again, to confirm it really was paid."""

    _connect()
    return stripe.checkout.Session.retrieve(session_id)

# --------------------

def verify_webhook(payload: bytes, signature: str):
    """Check a webhook really came from Stripe, and return the event.

    Without this the endpoint would take instructions from anyone who found
    the URL - including "this user paid, upgrade them". The signature is what
    makes it trustworthy, so a missing secret is a hard error rather than
    something to skip past.
    """

    _connect()
    secret = os.getenv("STRIPE_WEBHOOK_SECRET")

    if not secret:
        raise RuntimeError(
            "STRIPE_WEBHOOK_SECRET is missing from .env, so incoming webhooks "
            "can't be verified. See the README for how to get one."
        )

    return stripe.Webhook.construct_event(payload, signature, secret)
