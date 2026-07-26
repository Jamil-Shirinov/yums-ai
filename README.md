# 😋 Yums AI

Yums is a convenient platform that logs into your Gmail inbox, reads your unread emails, and uses AI (OpenAI's API) to sort them into two categories:

- **Actions** — emails that need you to *do* something (reply, approve, complete a task, make a decision, etc.)
- **Notices** — purely informational emails (newsletters, receipts, automated confirmations, etc.) that do not need a response.

The platform is **read-only**: Yums is respectful of your personal space and will never send, delete, or modify your emails. It is **view-only**.

Yums runs two ways:

- **As a web platform** (`app.py`) — companies sign up with an email and a plan, connect their Gmail once, and then get a report from a single button on a dashboard. No terminal required.
- **As a command-line tool** (`main.py`) — the original single-user script that prints a report and saves it as Markdown.

Both share the same Gmail fetching and classification code, so the results are identical — the web platform just presents them more neatly.

---

## Features
 
- Multi-user web dashboard: account signup, plans, one-click inbox analysis, and saved report history.
- Authenticates with Gmail via Google's standard OAuth2 flow (no passwords handled directly).
- Classifies and summarizes each unread email in a single OpenAI API call, using structured JSON output for reliable parsing.
- Outputs a formatted report to the terminal and saves a timestamped copy as a Markdown file.
- Fails safe: if a response cannot be parsed, the email is still surfaced (defaulted to "notice") rather than dropped.
- Includes a standalone test script for validating classification behavior against sample data, without requiring a live Gmail connection.

---

## Architecture
 
| Component | Responsibility |
|---|---|
| `gmail_client.py` | Desktop OAuth2 login (CLI) and unread message retrieval, used by both front ends. |
| `classifier.py` | Sends email content to OpenAI and returns a category, summary, and reason. |
| `models.py` | Shared dataclasses (`EmailMessage`, `EmailSummary`) used across modules. |
| `main.py` | CLI entry point. Orchestrates the pipeline (auth → fetch → classify → report). |
| `app.py` | Flask web app. Accounts, plans, dashboard, and the analysis route. |
| `gmail_oauth.py` | Web OAuth2 flow: per-user Gmail consent, token storage, and refresh. |
| `database.py` | SQLite schema and queries (accounts and saved analysis runs). |
| `plans.py` | Plan definitions and the per-analysis email limit each one grants. |
 
The core pipeline runs as follows: `gmail_client.py` retrieves unread messages and extracts sender, subject, date, and plain-text body. Each message is passed to `classifier.py`, which calls the OpenAI API with a structured system prompt and receives a JSON response (`category`, `summary`, `reason`).

From there the two front ends diverge. `main.py` aggregates the results, prints them to the terminal, and writes them to `output/` as Markdown. `app.py` saves them to SQLite against the logged-in account and renders them as a report page, so refreshing the page never re-runs (or re-bills) the analysis.

The split in Gmail authentication is worth calling out. The CLI uses a **Desktop app** OAuth client, which is allowed to open a browser on the same machine and listen on localhost — fine for one person on their own laptop. The web app uses a **Web application** OAuth client instead: it redirects the user to Google, receives them back at `/gmail/callback`, and stores that user's token against their account row. The two client types are configured separately (see Setup), so running one does not disturb the other.
 
Under the hood, Yums treats every unread message the same way. Yums is quiet, methodic, loyal, and with no opinions about your inbox habits 😊

---

## Prerequisites
 
- Python 3.10 or later
- A Gmail or Google Workspace account
- An [OpenAI API key](https://platform.openai.com/api-keys)

---

## Setup

**Important!** If you would like to use Yums AI for your inbox, please, first contact me at [jamil_shirinov@berkeley.edu](mailto:jamil_shirinov@berkeley.edu). Otherwise, Google OAuth will block you from connecting your Gmail.
### 1. Clone the repository and install dependencies

```bash
git clone https://github.com/Jamil-Shirinov/yums-ai.git
cd yums-ai
python3 -m venv anenv
source anenv/bin/activate      # on Windows: anenv\Scripts\activate
pip install -r requirements.txt
```

A virtual environment is recommended to isolate this project's dependencies.

### 2. Configure the OpenAI API key

Both the web platform and the CLI need this.

1. Generate a key at [platform.openai.com/api-keys](https://platform.openai.com/api-keys).
2. Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

3. Set `OPENAI_API_KEY` in `.env` to the generated key.
API usage is billed by OpenAI; processing an email typically costs a small fraction of a cent with the default model. Usage can be monitored at [platform.openai.com/usage](https://platform.openai.com/usage).

### 3. Enable the Gmail API (Google Cloud Console)

Needed for both front ends.

1. Go to [console.cloud.google.com](https://console.cloud.google.com/) and sign in with the Google account whose inbox will be accessed.
2. Create a new project (top-left project dropdown → "New Project").
3. Search for **"Gmail API"** in the search bar and click **Enable**.
4. Go to **APIs & Services → OAuth consent screen**.
- Choose **External** (unless using a Google Workspace org with Internal access).
- Fill in an app name, support email, and developer contact email.
- Skip adding scopes. Just click through to "Save and Continue".
- On the "Test users" step, add every Gmail address that will be used, as this is required while the app is in "Testing" mode.

Then set up credentials for whichever front end you want — the web platform (4a) or the CLI (4b). They use different OAuth client types and do not interfere with each other.

### 4a. Run the web platform

1. Go to **APIs & Services → Credentials → Create Credentials → OAuth client ID**.
- Application type: **Web application**.
- Under **Authorized redirect URIs**, add both of these:
  - `http://localhost:5000/gmail/callback`
  - `http://127.0.0.1:5000/gmail/callback`
- Click **Create**. Copy the client ID and client secret from the dialog.

2. In `.env`, fill in:

```
FLASK_SECRET_KEY=...        # python -c "import secrets; print(secrets.token_hex(32))"
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
OAUTHLIB_INSECURE_TRANSPORT=1
OAUTHLIB_RELAX_TOKEN_SCOPE=1
```

The last two lines are for local development only. Google's OAuth library refuses to run over plain `http`, and the Flask dev server is `http://localhost` — remove both once the app is deployed behind https.

3. Start the server:

```bash
python app.py
```

4. Open [http://localhost:5000](http://localhost:5000), sign up with an email and a plan, then click **Connect Gmail** on the dashboard. After approving access, the dashboard's **Analyze my inbox** button produces the report.

The SQLite database (`yums.db`) is created automatically on first run.

> The redirect URI must match *exactly*, including the port. If you open the app at `127.0.0.1` but only registered `localhost` (or you run on a different port), Google returns `redirect_uri_mismatch`.

### 4b. Run the command line tool

1. Go to **APIs & Services → Credentials → Create Credentials → OAuth client ID**.
- Application type: **Desktop app**.
- Click **Create**, then **Download JSON** on the resulting credential.
2. Rename the downloaded file to `credentials.json` and place it in the project root, next to `main.py`.
3. Run it:

```bash
python main.py
```

On the first run, a browser window opens for Google OAuth login and consent. The resulting token is cached in `token.json`, so subsequent runs do not require re-authentication.

---

## Usage

### Web platform

| Page | What it does |
|---|---|
| `/` | Landing page and plan comparison. |
| `/signup` | Create an account and choose a plan. |
| `/dashboard` | Connect or disconnect Gmail, run an analysis, browse past reports. |
| `/results/<id>` | A saved report, split into Actions Needed and Notices. |

Plans are defined in `plans.py` and control how many unread emails one analysis covers (Free 5, Pro 25, Business 100). Editing that file is all it takes to change the tiers.

**Plans are simulated.** Choosing one at signup records a name on the account and sets that limit — no payment processor is involved and no card is charged. Wiring up real billing means adding a checkout step to `/signup`; nothing else in the app needs to change, since every other route just reads `user["plan"]`.

### Command line

```bash
python main.py                         # process up to 20 unread emails (default)
python main.py --limit 10              # limit processing to the 10 most recent unread emails
python main.py --mark-as-read          # currently a no-op; see note below
```

`--mark-as-read` is defined but intentionally non-functional. Because the project only requests the `gmail.readonly` scope, it cannot modify message state under any circumstances. Enabling this would require changing `SCOPES` in `gmail_client.py` to `gmail.modify`, deleting the cached `token.json` to force re-consent, and implementing a call to `service.users().messages().modify(...)` in `main.py`.

### Testing

Neither test script needs Gmail, and only `test_classifier.py` costs anything.

```bash
python test_web.py            # exercise every web route (no Gmail, no OpenAI, free)
python test_web.py --demo     # add a demo account + sample report to yums.db
python test_classifier.py     # run the classifier on sample emails (uses OpenAI credit)
```

`test_web.py` drives the real routes, templates, and database queries through Flask's test client. It covers signup validation, password hashing, login, access control, report rendering, and the fact that one account cannot open another account's report. It runs against a temporary database, so `yums.db` is left untouched.

`test_web.py --demo` is the one that writes to `yums.db`: it creates `demo@yums.ai` / `demopassword` on the Business plan with a sample report attached, so the dashboard and report pages can be viewed in a browser before any Gmail setup is done.

`test_classifier.py` runs the classifier against a set of hardcoded sample emails and prints the resulting categorization.

---

## Project structure
 
| File | Purpose |
|---|---|
| `app.py` | Web platform entry point. Flask routes, sessions, and login handling. |
| `gmail_oauth.py` | Web OAuth2 flow and per-user token refresh. |
| `database.py` | SQLite schema and queries. |
| `plans.py` | Plan definitions and per-analysis email limits. |
| `templates/` | Jinja templates (`base`, `index`, `signup`, `login`, `dashboard`, `results`). |
| `static/style.css` | Styling for the web platform. |
| `main.py` | CLI entry point. Runs the full pipeline and handles CLI arguments. |
| `gmail_client.py` | Gmail OAuth2 authentication (desktop) and unread message retrieval. |
| `classifier.py` | OpenAI API integration for classification and summarization. |
| `models.py` | Shared data structures. |
| `test_web.py` | Web platform test harness, plus a `--demo` account seeder. |
| `test_classifier.py` | Classifier test harness using sample data. |
| `.env.example` | Template for required environment variables. |
| `credentials.json` | Google OAuth client credentials for the CLI (user-generated, not included). |
| `token.json` | Cached CLI OAuth token, created after first login. |
| `yums.db` | SQLite database for the web platform, created on first run. |
| `output/` | Destination for generated Markdown reports (CLI only). |

---

## Security & privacy
 
- Only the `gmail.readonly` OAuth scope is requested. The application cannot send, delete, or modify email.
- `.env`, `credentials.json`, `token.json`, and `yums.db` contain sensitive data and are excluded via `.gitignore`. These files should never be committed to version control.
- Email subject lines, sender addresses, and body content are transmitted to OpenAI's API for classification. This tool should not be used on inboxes containing content that should not be shared with a third-party API provider.
- Passwords are stored as salted hashes via Werkzeug's `generate_password_hash`. The plaintext password is never written to disk.
- Saved reports deliberately store only the sender, subject, date, category, and summary. Email **bodies are discarded** after classification rather than kept in the database.
- Reports are scoped to their owner at the query level (`WHERE id = ? AND user_id = ?`), so changing the number in a `/results/<id>` URL cannot expose another account's report.
- Session cookies are `HttpOnly` and `SameSite=Lax`, which keeps another site from making a logged-in browser POST to `/analyze`.

### Before running this in production

The web platform is a working implementation, not a hardened deployment. At minimum, the following would need addressing first:

- **Gmail tokens are stored unencrypted** in the SQLite database. A copy of `yums.db` grants read access to every connected inbox. These should be encrypted at rest, ideally with a key held outside the database.
- **Serve over HTTPS** behind a real WSGI server (gunicorn, waitress). `python app.py` starts Flask's development server, which is single-threaded and not built for real traffic. Then remove `OAUTHLIB_INSECURE_TRANSPORT` and set `SESSION_COOKIE_SECURE = True`.
- **Analysis runs synchronously** inside the request. At one OpenAI call per email, a 100-email Business run can take a couple of minutes and will hit most hosting platforms' request timeouts. This wants a background job queue.
- **No rate limiting or login throttling** exists, so nothing slows down repeated password guesses or someone hammering the analyze button.
- **Plans are not enforced by payment.** Anyone can pick Business at signup for free until real billing is wired in.

---

## Troubleshooting
 
**`FileNotFoundError: Couldn't find 'credentials.json'`**
The Google Cloud Console setup was not completed, or the file is not named exactly `credentials.json` in the project root.

**`No OPENAI_API_KEY found`**
Confirm that `.env.example` was copied to `.env` (not renamed) and that a valid key was set.

**Browser login displays "app isn't verified"**
Expected while the OAuth consent screen is in "Testing" mode. Click **Advanced → Go to [app name] (unsafe)** to proceed.

**"No unread emails found"**
Indicates normal operation — there are simply no unread messages in the inbox at the time of the run.

**`redirect_uri_mismatch` when connecting Gmail on the web platform**
The redirect URI registered in Google Cloud Console must match the one the app sends, exactly. Register both `http://localhost:5000/gmail/callback` and `http://127.0.0.1:5000/gmail/callback`, and if you run on a different port, register that too.

**`InsecureTransportError` / "OAuth 2 MUST utilize https"**
`OAUTHLIB_INSECURE_TRANSPORT=1` is missing from `.env`. It is required for local http development and should be removed in production.

**`Scope has changed` when connecting Gmail**
Google occasionally returns extra granted scopes. Set `OAUTHLIB_RELAX_TOKEN_SCOPE=1` in `.env`.

**`GOOGLE_CLIENT_ID and/or GOOGLE_CLIENT_SECRET are missing`**
The web platform uses a Web application OAuth client configured in `.env`, not the CLI's `credentials.json`. See step 4a.

**"This Gmail connection is no longer valid"**
The stored token was revoked (from the user's Google account settings) or expired without a refresh token. Click **Disconnect Gmail**, then connect again.

---
 
## Roadmap

- Real payment processing (Stripe Checkout) in place of the currently simulated plans.
- Background job queue so large analyses don't block the request.
- Encryption at rest for stored Gmail tokens.
- `--since` flag to limit processing to a recent time window.
- Support for local/open-source models (e.g. via Ollama) as an alternative to OpenAI.
- Priority/urgency scoring for action items.
- Scheduled execution (e.g. daily)

---
 
## Contributions
 
Issues and pull requests are welcome. Contributions that expand Gmail's permission scope beyond read-only should be optional and clearly documented, not default behavior.

## License

No license yet. Until a license is added, all rights for the project and repository are reserved by [Jamil Shirinov](https://jshirinov.com), the repository owner.
