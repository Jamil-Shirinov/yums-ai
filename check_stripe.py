"""
Check the Stripe setup matches what the site advertises.

Prices live in two places: plans.py decides what the pricing page says, and
Stripe decides what the card is actually charged. Nothing keeps them in step
automatically, so this compares them and complains about any disagreement -
which is the one mistake here that costs real money.

    python check_stripe.py

Read-only: it looks things up and charges nobody.
"""

import os
import sys

from dotenv import load_dotenv

from plans import PLANS, STRIPE_PRICE_VARIABLES

load_dotenv()

# --------------------

def parse_amount(price_text: str) -> int:
    """'$2.99' -> 299, because Stripe works in whole cents."""

    return round(float(price_text.replace("$", "").strip()) * 100)


def parse_interval(period_text: str) -> str:
    """'per month' -> 'month', which is what Stripe calls it."""

    if "year" in period_text:
        return "year"
    if "month" in period_text:
        return "month"
    return period_text.strip()

# --------------------

def main():
    problems = []

    secret_key = os.getenv("STRIPE_SECRET_KEY")
    if not secret_key:
        print("STRIPE_SECRET_KEY isn't set, so payments are switched off.")
        print("Plans currently change without anyone being charged.")
        return 1

    import stripe

    import billing

    stripe.api_key = secret_key

    mode = "TEST" if secret_key.startswith("sk_test_") else "LIVE"
    print(f"Stripe key: {mode} mode")

    try:
        # as_dict because Stripe's own objects don't support .get().
        account = billing.as_dict(stripe.Account.retrieve())
        print(f"Connected to: {account.get('email') or account.get('id')}")
    except Exception as error:
        print(f"\nThe secret key was rejected -> {type(error).__name__}: {error}")
        return 1

    if mode == "LIVE":
        print("\n!! This is a LIVE key. Real cards will be charged. !!")

    print()
    print(f"{'plan':<18} {'expected':<16} {'stripe says':<16} status")
    print("-" * 62)

    for plan_id, variable in STRIPE_PRICE_VARIABLES.items():
        plan = PLANS[plan_id]
        want_amount = parse_amount(plan["price"])
        want_interval = parse_interval(plan["period"])
        expected = f"${want_amount / 100:.2f}/{want_interval}"

        price_id = os.getenv(variable)
        if not price_id:
            print(f"{plan_id:<18} {expected:<16} {'-':<16} not set up, so it can't be bought")
            continue

        try:
            price = billing.as_dict(stripe.Price.retrieve(price_id))
        except Exception as error:
            print(f"{plan_id:<18} {expected:<16} {'?':<16} FAILED to look up ({error.__class__.__name__})")
            problems.append(f"{variable} ({price_id}) doesn't exist in this Stripe account")
            continue

        recurring = price.get("recurring") or {}
        got_amount = price.get("unit_amount")
        got_interval = recurring.get("interval")
        actual = f"${(got_amount or 0) / 100:.2f}/{got_interval or 'one-off'}"

        if not recurring:
            print(f"{plan_id:<18} {expected:<16} {actual:<16} NOT a subscription")
            problems.append(f"{variable} is a one-off price; it must be recurring")
        elif got_amount != want_amount or got_interval != want_interval:
            print(f"{plan_id:<18} {expected:<16} {actual:<16} MISMATCH")
            problems.append(
                f"{plan_id}: the site says {expected} but Stripe charges {actual}"
            )
        elif not price.get("active"):
            print(f"{plan_id:<18} {expected:<16} {actual:<16} price is archived")
            problems.append(f"{variable} points at an archived price")
        else:
            print(f"{plan_id:<18} {expected:<16} {actual:<16} ok")

    print()
    if not os.getenv("STRIPE_WEBHOOK_SECRET"):
        print("STRIPE_WEBHOOK_SECRET isn't set, so webhooks will be refused and")
        print("nobody's plan would be activated. Run:")
        print("    stripe listen --forward-to localhost:5000/stripe/webhook")
        print("and copy the whsec_... it prints into .env.")
        problems.append("STRIPE_WEBHOOK_SECRET is missing")
    else:
        print("Webhook signing secret: set")

    print()
    if problems:
        print(f"{len(problems)} problem(s) to fix:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print("Stripe setup looks right.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
