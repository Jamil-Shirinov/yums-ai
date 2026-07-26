"""
The payment plans a company can sign up for.

Heads up: none of this talks to a real payment processor. Picking a plan at
signup just records a name on the account. What the plan *actually* controls
is how many unread emails a single analysis run is allowed to look at, which
keeps the OpenAI bill predictable.

The annual plans are separate entries rather than a flag on the monthly ones,
so the account still only needs to remember a single plan name. Both Pro
entries share the display name "Pro", so the dashboard shows the tier without
caring how the customer is billed.

Swapping this out for real Stripe checkout later only means adding a payment
step to /signup - the rest of the app just reads user["plan"].
"""

PLANS = {
    "free": {
        "name": "Free",
        "price": "$0",
        "period": "forever",
        "savings": "",
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
        "price": "$2.99",
        "period": "per month",
        "savings": "",
        "email_limit": 25,
        "blurb": "For one busy person with a real inbox.",
        "features": [
            "25 unread emails per analysis",
            "Actions vs. Notices breakdown",
            "Analysis history",
            "1 connected Gmail account",
        ],
    },
    "pro_annual": {
        "name": "Pro",
        "price": "$29.99",
        "period": "per year",
        "savings": "Save $6",
        "email_limit": 25,
        "blurb": "Pro, billed once a year.",
        "features": [
            "25 unread emails per analysis",
            "Actions vs. Notices breakdown",
            "Analysis history",
            "1 connected Gmail account",
        ],
    },
    "business": {
        "name": "Business",
        "price": "$7.99",
        "period": "per month",
        "savings": "",
        "email_limit": 100,
        "blurb": "For teams drowning in shared inboxes.",
        "features": [
            "100 unread emails per analysis",
            "Actions vs. Notices breakdown",
            "Analysis history",
            "Priority support",
        ],
    },
    "business_annual": {
        "name": "Business",
        "price": "$85.99",
        "period": "per year",
        "savings": "Save $9",
        "email_limit": 100,
        "blurb": "Business, billed once a year.",
        "features": [
            "100 unread emails per analysis",
            "Actions vs. Notices breakdown",
            "Analysis history",
            "Priority support",
        ],
    },
}

# The three tiers shown on the pricing page, each paired with its annual
# version. Free has no annual version, so it pairs with None.
TIERS = [
    ("free", None),
    ("pro", "pro_annual"),
    ("business", "business_annual"),
]

DEFAULT_PLAN = "free"

# --------------------

def get_plan(plan_id: str) -> dict:
    """Look up a plan by its id, falling back to Free if the name isn't one
    we recognise (e.g. an old account from before a plan was renamed)."""

    return PLANS.get(plan_id, PLANS[DEFAULT_PLAN])
