# Privacy Policy for Yokai

**Last Updated:** September 23, 2026

This Privacy Policy explains how Yokai collects, uses, and safeguards information when operating within Discord. Yokai is a private Discord bot designed with data minimization as a core architectural principle.

---

## 1. Information Collected

Yokai collects only the minimal data strictly necessary to provide music streaming, queue management, and basic server telemetry:

- **Discord User Identifiers:** Discord User IDs (numeric snowflakes) associated with users who execute slash commands or request songs.
- **Command Logs (`command_log`):** Timestamp (UTC), Discord User ID, and the command name executed (e.g., `/play`, `/skip`, `/queue`).
- **Playback Telemetry (`play_events`):** Timestamp (UTC), requester User ID, YouTube video ID, YouTube-derived title and artist, track duration, origin category (`link`, `search`, `import`, or `recommendation`), playback outcome (`completed`, `skipped`, `stopped`, or `error`), and total seconds listened.
- **Functional Match Cache (`spotify_match_cache`):** Cached mappings between public Spotify track IDs and resolved YouTube video IDs, stored strictly to optimize performance and prevent duplicate lookups.

---

## 2. Information NOT Collected

Yokai strictly enforces privacy guardrails:
- **No Message Content:** Yokai does not request or possess the privileged Message Content intent. It cannot read, log, or inspect user messages in channels.
- **No Voice Recording:** Yokai transmits real-time audio streams into Discord voice channels via DAVE end-to-end encryption. It does not record, capture, or analyze incoming voice audio from users.
- **No Personal Identifiable Information (PII):** Yokai never collects real names, email addresses, IP addresses, payment details, or personal profile data.
- **No Third-Party Sharing or ML Training:** Data stored by Yokai is never sold, shared with third parties, or used to train external machine learning models. Spotify-derived metadata is never written to analytics tables.

---

## 3. Storage and Security

- All bot data is stored locally in an SQLite database on the bot owner's private host machine.
- Database files and operational logs are strictly excluded from version control and public repositories.
- Sensitive access tokens and operational secrets are scrubbed from log files via automated redaction filters.

---

## 4. Data Retention and Deletion

- Data is retained solely for historical usage statistics (such as the `/usage` command) and recommendation deduplication.
- Users may request the deletion of their associated user records, command logs, or play events at any time by contacting the bot owner directly.

---

## 5. Contact

For inquiries regarding this Privacy Policy or to submit a data deletion request, please reach out to the bot owner on Discord.
