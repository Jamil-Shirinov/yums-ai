# 😋 Yums AI

Yums is a convenient platform that logs into your Gmail inbox, reads your unread
emails, and uses AI (OpenAI's API) to sort them into two categories:

- **Actions** — emails that need you to *do* something (reply, approve, complete a task, make a decision, etc.)
- **Notices** — purely informational emails (newsletters, receipts, automated confirmations, etc. ) that do not need a response.

The platform is **read-only**: Yums is respectful of your personal space and will never send, delete, or modify your emails. It is **view-only**.

---

## Features
 
- Authenticates with Gmail via Google's standard OAuth2 flow (no passwords handled directly).
- Classifies and summarizes each unread email in a single OpenAI API call, using structured JSON output for reliable parsing.
- Outputs a formatted report to the terminal and saves a timestamped copy as a Markdown file.
- Fails safe: if a response cannot be parsed, the email is still surfaced (defaulted to "notice") rather than dropped.
- Includes a standalone test script for validating classification behavior against sample data, without requiring a live Gmail connection.

---

## Architecture
 
| Component | Responsibility |
|---|---|
| `gmail_client.py` | Handles OAuth2 authentication and fetches unread messages from the inbox. |
| `classifier.py` | Sends email content to OpenAI and returns a category, summary, and reason. |
| `main.py` | Orchestrates the pipeline (auth → fetch → classify → report) and handles CLI arguments. |
| `models.py` | Shared dataclasses (`EmailMessage`, `EmailSummary`) used across modules. |
 
The pipeline runs as follows: `gmail_client.py` retrieves unread messages and extracts sender, subject, date, and plain-text body. Each message is passed to `classifier.py`, which calls the OpenAI API with a structured system prompt and receives a JSON response (`category`, `summary`, `reason`). `main.py` aggregates the results, prints them to the terminal, and writes them to `output/` as Markdown.
 
Under the hood, Yums treats every unread message the same way. Yums is quiet, methodic, loyal, and with no opinions about your inbox habits 😊

---

## Prerequisites
 
- Python 3.10 or later
- A Gmail or Google Workspace account
- An [OpenAI API key](https://platform.openai.com/api-keys)

---

## Setup

### 1. Clone the repository and install dependencies

```bash
git clone https://github.com/Jamil-Shirinov/yums-ai.git
cd yums-ai
python3 -m venv venv
source venv/bin/activate      # on Windows: venv\Scripts\activate
pip install -r requirements.txt
```

A virtual environment is recommended to isolate this project's dependencies.

### 2. Configure Gmail API access (Google Cloud Console)

1. Go to [console.cloud.google.com](https://console.cloud.google.com/) and sign in with the Google account whose inbox will be accessed.
2. Create a new project (top-left project dropdown → "New Project").
3. Search for **"Gmail API"** in the search bar and click **Enable**.
4. Go to **APIs & Services → OAuth consent screen**.
- Choose **External** (unless using a Google Workspace org with Internal access).
- Fill in an app name, support email, and developer contact email.
- Skip adding scopes. Just click through to "Save and Continue".
- On the "Test users" step, add the Gmail address that will be used, as this is required while the app is in "Testing" mode.
5. Go to **APIs & Services → Credentials → Create Credentials → OAuth client ID**.
- Application type: **Desktop app**.
- Click **Create**, then **Download JSON** on the resulting credential.
6. Rename the downloaded file to `credentials.json` and place it in the project root, next to `main.py`.
> The app remains in "Testing" mode by default, which is sufficient for personal use. Google restricts access to the explicitly added test users.

### 3. Configure the OpenAI API key

1. Generate a key at [platform.openai.com/api-keys](https://platform.openai.com/api-keys).
2. Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

3. Set `OPENAI_API_KEY` in `.env` to the generated key.
API usage is billed by OpenAI; processing an email typically costs a small fraction of a cent with the default model. Usage can be monitored at [platform.openai.com/usage](https://platform.openai.com/usage).

### 4. Run

```bash
python main.py
```

On the first run, a browser window opens for Google OAuth login and consent. The resulting token is cached in `token.json`, so subsequent runs do not require re-authentication.

---

## Usage

```bash
python main.py                         # process up to 20 unread emails (default)
python main.py --limit 10              # limit processing to the 10 most recent unread emails
python main.py --mark-as-read          # currently a no-op; see note below
```

`--mark-as-read` is defined but intentionally non-functional. Because the project only requests the `gmail.readonly` scope, it cannot modify message state under any circumstances. Enabling this would require changing `SCOPES` in `gmail_client.py` to `gmail.modify`, deleting the cached `token.json` to force re-consent, and implementing a call to `service.users().messages().modify(...)` in `main.py`.

To validate classification behavior without connecting to a live inbox:

```bash
python test_classifier.py
```

This runs the classifier against a set of hardcoded sample emails and prints the resulting categorization.

---

## Project structure
 
| File | Purpose |
|---|---|
| `main.py` | Entry point. Runs the full pipeline and handles CLI arguments. |
| `gmail_client.py` | Gmail OAuth2 authentication and unread message retrieval. |
| `classifier.py` | OpenAI API integration for classification and summarization. |
| `models.py` | Shared data structures. |
| `test_classifier.py` | Classifier test harness using sample data. |
| `.env.example` | Template for required environment variables. |
| `credentials.json` | Google OAuth client credentials (user-generated, not included). |
| `token.json` | Cached OAuth token, created after first login. |
| `output/` | Destination for generated Markdown reports. |

---

## Security & privacy
 
- Only the `gmail.readonly` OAuth scope is requested. The application cannot send, delete, or modify email.
- `.env`, `credentials.json`, and `token.json` contain sensitive data and are excluded via `.gitignore`. These files should never be committed to version control.
- Email subject lines, sender addresses, and body content are transmitted to OpenAI's API for classification. This tool should not be used on inboxes containing content that should not be shared with a third-party API provider.

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

---
 
## Roadmap

- Web-based UI as an alternative to terminal + Markdown output (Flask/FastAPI).
- `--since` flag to limit processing to a recent time window.
- Support for local/open-source models (e.g. via Ollama) as an alternative to OpenAI.
- Priority/urgency scoring for action items.
- Scheduled execution (e.g. daily)

---
 
## Contributions
 
Issues and pull requests are welcome. Contributions that expand Gmail's permission scope beyond read-only should be optional and clearly documented, not default behavior.

## License

No license yet. Until a license is added, all rights for the project and repository are reserved by [Jamil Shirinov](https://jshirinov.com), the repository owner.
