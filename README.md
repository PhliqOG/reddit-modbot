# reddit-modbot

A small, open-source, **non-commercial** Reddit moderation bot for a single subreddit. It enforces a post-title format and blocks links to domains on a moderator-maintained blacklist. It is built with [PRAW](https://praw.readthedocs.io/) and licensed under MIT.

## Purpose and community benefit

Moderators spend a lot of time on repetitive work: telling people their title is missing a required tag, and removing obvious spam links. This bot does exactly those two things, consistently and politely, so human moderators can spend their time on judgment calls.

- **Formatting enforcement:** posts whose title doesn't start with a `[Tag]` are removed, and the author gets a friendly comment explaining how to fix it and how to contact the moderators.
- **Spam reduction:** link posts pointing at a blacklisted domain (or its subdomains) are removed with an explanatory comment.

The bot only reads new submissions in the one subreddit it moderates. Its only actions are `submission.mod.remove()` and one explanatory reply. Moderators' own posts and posts a moderator has approved are never touched. It makes no votes, sends no messages and does no crawling.

## Non-commercial and privacy notice

- This project is **non-commercial**. It is not sold, monetized, or used to promote any product or service.
- **No user data is collected, sold or shared.** The bot doesn't store anything persistently: no database, no files, no log files. Log output (stderr only) contains a violation type and a post ID, never titles, usernames or post content.
- **Nothing is used for AI/LLM training**, and the bot doesn't use any AI or machine-learning service.
- Removal comments are fixed text; nothing from a post is repeated back.
- Credentials come only from environment variables and are never committed or logged.

## Setup

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Configuration

1. Create a **dedicated bot account**, make it a moderator of your subreddit with "Posts" permission only, and create a "script" app at <https://www.reddit.com/prefs/apps>.
2. Copy the template and fill it in:

   ```bash
   cp .env.example .env
   ```

   `.env.example` explains where each value comes from. The required variables are `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT`, `REDDIT_USERNAME`, `REDDIT_PASSWORD` and `TARGET_SUBREDDIT`. `.env` is git-ignored.
3. Add domains to block in `blacklist.txt` (one per line).
4. To change the title rule, edit `TITLE_PATTERN` at the top of `main.py`.

## Run

```bash
DRY_RUN=1 python main.py   # log what would be removed, change nothing (Windows: set DRY_RUN=1 first)
python main.py             # live
```

Rate limits (HTTP 429) and transient server errors are retried with exponential backoff, following Reddit's API guidelines.

## Tests

```bash
pip install pytest
pytest
```

## License

MIT, see [LICENSE](LICENSE).
