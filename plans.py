"""
The payment plans a company can sign up for.

Heads up: none of this talks to a real payment processor. Picking a plan at
signup just records a name on the account. What the plan *actually* controls
is how many unread emails a single analysis run is allowed to look at, which
keeps the OpenAI bill predictable.

Swapping this out for real Stripe checkout later only means adding a payment
step to /signup - the rest of the app just reads user["plan"].
"""

PLANS = {
    "free": {
        "name": "Free",
        "price": "$0",
        "period": "forever",
        "email_limit": 5,
        "blurb": "Try it on a corner of your inbox.",
        "features": [
            "5 unread emails per analysis",
            "Actions vs. Notices breakdown",
            "1 connected Gmail account",
        ],
    },
    "pro": {
        "name": "Pro",
        "price": "$19",
        "period": "per month",
        "email_limit": 25,
        "blurb": "For one busy person with a real inbox.",
        "features": [
            "25 unread emails per analysis",
            "Actions vs. Notices breakdown",
            "Analysis history",
            "1 connected Gmail account",
        ],
    },
    "business": {
        "name": "Business",
        "price": "$49",
        "period": "per month",
        "email_limit": 100,
        "blurb": "For teams drowning in shared inboxes.",
        "features": [
            "100 unread emails per analysis",
            "Actions vs. Notices breakdown",
            "Analysis history",
            "Priority support",
        ],
    },
}

DEFAULT_PLAN = "free"

# --------------------

def get_plan(plan_id: str) -> dict:
    """Look up a plan by its id, falling back to Free if the name isn't one
    we recognise (e.g. an old account from before a plan was renamed)."""

    return PLANS.get(plan_id, PLANS[DEFAULT_PLAN])
