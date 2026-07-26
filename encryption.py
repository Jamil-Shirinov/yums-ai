"""
Encrypting Gmail tokens before they go anywhere near the database.

A stored Gmail token is a live key to somebody's inbox. Kept as plain text,
anyone who gets hold of yums.db - a stray backup, a copied file, a leaked
disk image - can read every connected mailbox. Encrypting means the database
file on its own is useless without the key, which lives in .env instead.

This uses Fernet from the cryptography package: AES in CBC mode with an
HMAC over the result, so a tampered value fails to decrypt rather than
quietly returning something wrong.

The key is the whole game. Lose it and no stored token can be read back;
every connected user simply has to press Connect Gmail again. Leak it
alongside the database and you're back to plain text.
"""

import os

from cryptography.fernet import Fernet, InvalidToken

KEY_VARIABLE = "YUMS_ENCRYPTION_KEY"

# --------------------

def generate_key() -> str:
    """Make a brand new key, for pasting into .env."""

    return Fernet.generate_key().decode()

# --------------------

def _cipher() -> Fernet:
    """Build the cipher from the key in the environment."""

    key = os.getenv(KEY_VARIABLE)

    if not key:
        raise RuntimeError(
            f"{KEY_VARIABLE} is missing from .env.\n\n"
            f"Gmail tokens are encrypted before being stored, so the app needs "
            f"a key to read them back. Add this line to .env - a fresh key, "
            f"generated just now:\n\n"
            f"    {KEY_VARIABLE}={generate_key()}\n\n"
            f"Keep it somewhere safe. If it changes, every connected user has "
            f"to reconnect their Gmail."
        )

    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as error:
        raise RuntimeError(
            f"{KEY_VARIABLE} isn't a valid key ({error}). It should be the "
            f"44-character string produced by:\n\n"
            f"    python -c \"import encryption; print(encryption.generate_key())\""
        )

# --------------------

def ensure_key() -> None:
    """Check the key at startup, so a missing one is an obvious error on
    launch rather than a surprise the first time somebody connects Gmail."""

    _cipher()

# --------------------

def encrypt(plaintext: str) -> str:
    """Scramble a token for storage."""

    return _cipher().encrypt(plaintext.encode()).decode()

# --------------------

def is_encrypted(stored: str) -> bool:
    """Tell an encrypted value from a leftover plain-text one.

    Saved tokens are JSON, so they always start with "{". Fernet output is
    base64 and never does.
    """

    return bool(stored) and not stored.startswith("{")

# --------------------

def decrypt(stored: str) -> str:
    """Read a stored token back.

    Values written before encryption existed are returned as-is, so an older
    database keeps working until init_db() gets around to encrypting them.
    """

    if not is_encrypted(stored):
        return stored

    try:
        return _cipher().decrypt(stored.encode()).decode()
    except InvalidToken:
        # Wrong key, or the stored value was tampered with. Either way we
        # can't recover it, and the only fix is to connect again.
        raise RuntimeError(
            "This Gmail connection can't be read back - the encryption key has "
            "changed since it was saved. Please disconnect and reconnect Gmail."
        )
