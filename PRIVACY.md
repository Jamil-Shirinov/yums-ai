# Privacy Policy

_Last updated: July 19, 2026_

Yums AI ("Yums", "the app") is a local, open-source command-line tool that reads your unread Gmail messages and uses OpenAI's API to sort them into "Actions" and "Notices." This page explains what data the app accesses and what happens to it.

## What Yums accesses

When you authorize Yums via Google Sign-In, it requests exactly one scope:

- `https://www.googleapis.com/auth/gmail.readonly`

This is **read-only**. Yums cannot send, delete, modify, or label your emails. The code does not call any Gmail write endpoints.

For each unread message in your inbox, Yums reads the sender, subject, date, and plain-text body (truncated to 4,000 characters).

## What Yums does with that data

1. Each email's sender/subject/date/body is sent, one message at a time, to OpenAI's API (`gpt-4o-mini`) using **your own OpenAI API key**, which you supply yourself, so that the model can classify it as an "action" or "notice" and write a short summary.
2. The classified results are printed to your terminal and saved as a timestamped Markdown file in a local `output/` folder on your own machine.

## Where your data is stored

Yums runs entirely on your own computer. There is no Yums server.

- Your Google OAuth token is cached locally in `token.json` on your machine so you do not have to re-authenticate every run. It is never transmitted to the developer or any third party other than Google.
- Email content sent for classification goes directly from your machine to OpenAI's API, governed by [OpenAI's API data usage policies](https://openai.com/policies/api-data-usage-policies). It does not pass through, or get stored on, any server operated by the developer of Yums.
- Report output (`output/*.md`) stays on your machine unless you choose to share it.

The developer of Yums (Jamil Shirinov) does not receive, view, or store any user's email content or Gmail credentials.

## Third parties involved

- **Google (Gmail API)** — used to read your unread messages, governed by [Google's Privacy Policy](https://policies.google.com/privacy) and the [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy). Yums's use and transfer of information received from Google APIs adheres to this policy, including the Limited Use requirements.
- **OpenAI** — used to classify/summarize email content, governed by [OpenAI's Privacy Policy](https://openai.com/privacy/).

## Your control over your data

- You can revoke Yums's access at any time from your [Google Account permissions page](https://myaccount.google.com/permissions).
- You can delete all locally cached data by deleting `token.json` and the `output/` folder from the project directory.
- Because everything runs locally, deleting these files and revoking access removes all traces of Yums's access to your account.

## Changes to this policy

If this policy changes, the updated version will be posted at this same URL
with a new "Last updated" date.

## Contact

Questions about this policy or the app shall be directed to **[jamil_shirinov@berkeley.edu](mailto:jamil_shirinov@berkeley.edu)**.
