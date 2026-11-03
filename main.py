"""Non-commercial moderation bot: enforces a title format and a domain blacklist on /new."""

import logging
import os
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import praw
from dotenv import load_dotenv
from praw.exceptions import RedditAPIException
from prawcore.exceptions import PrawcoreException, RequestException, ServerError, TooManyRequests

REQUIRED_ENV = (
    "REDDIT_CLIENT_ID",
    "REDDIT_CLIENT_SECRET",
    "REDDIT_USER_AGENT",
    "REDDIT_USERNAME",
    "REDDIT_PASSWORD",
    "TARGET_SUBREDDIT",
)

# Titles must start with a [Tag], e.g. "[Question] How do I ...". Edit to suit your subreddit.
TITLE_PATTERN = re.compile(r"^\[[^\]]{1,30}\]\s*\S")
BLACKLIST_FILE = Path(__file__).with_name("blacklist.txt")

MAX_RETRIES = 6
MAX_DELAY = 300  # seconds

# Comments are fixed text. Nothing from the post (title, URL, author) is ever echoed back.
COMMENTS = {
    "title": (
        "Hello! Your post was automatically removed because its title doesn't follow this "
        "community's format. Titles must start with a tag in square brackets, for example "
        "`[Question] Your title here`.\n\n"
        "Please feel free to resubmit with a corrected title. If you think this was a mistake, "
        "you can [message the moderators](https://www.reddit.com/message/compose?to=/r/{subreddit}).\n\n"
        "*I'm a bot, and this action was performed automatically.*"
    ),
    "domain": (
        "Hello! Your post was automatically removed because it links to a website that is not "
        "allowed in this community.\n\n"
        "If you think this was a mistake, you can "
        "[message the moderators](https://www.reddit.com/message/compose?to=/r/{subreddit}).\n\n"
        "*I'm a bot, and this action was performed automatically.*"
    ),
}

log = logging.getLogger("modbot")


def load_blacklist(path=BLACKLIST_FILE):
    """Read blocked domains, one per line. '#' starts a comment."""
    if not Path(path).exists():
        log.warning("no blacklist file found; domain filtering is off")
        return set()
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return {d for d in (line.split("#", 1)[0].strip().lower() for line in lines) if d}


def title_ok(title):
    return bool(TITLE_PATTERN.match(title))


def blacklisted(url, domains):
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in domains)


def violation(submission, domains):
    """Return 'title' or 'domain' for the first rule broken, else None."""
    if not title_ok(submission.title):
        return "title"
    if not submission.is_self and blacklisted(submission.url, domains):
        return "domain"
    return None


def with_backoff(call, sleep=time.sleep):
    """Run call(); on rate limits or transient errors retry with exponential backoff."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            return call()
        except (TooManyRequests, ServerError, RequestException, RedditAPIException) as exc:
            if isinstance(exc, RedditAPIException) and not any(
                item.error_type == "RATELIMIT" for item in exc.items
            ):
                raise
            if attempt == MAX_RETRIES:
                raise
            delay = min(2**attempt, MAX_DELAY) + random.uniform(0, 1)
            try:
                delay = max(delay, min(float(getattr(exc, "retry_after", None) or 0), MAX_DELAY))
            except ValueError:
                pass
            log.warning("transient API error (%s); retrying in %.0fs", type(exc).__name__, delay)
            sleep(delay)


def should_skip(submission, mods):
    """Leave alone: deleted authors, moderators, approved posts, already-removed posts."""
    author = submission.author
    if author is None or author.name.lower() in mods:
        return True
    return bool(getattr(submission, "approved_by", None)) or bool(
        getattr(submission, "removed_by_category", None)
    )


def handle(submission, domains, mods, subreddit_name, dry_run=False):
    if should_skip(submission, mods):
        return
    reason = violation(submission, domains)
    if reason is None:
        return
    log.info("violation=%s post=%s dry_run=%s", reason, submission.id, dry_run)
    if dry_run:
        return
    with_backoff(submission.mod.remove)
    with_backoff(lambda: submission.reply(COMMENTS[reason].format(subreddit=subreddit_name)))


def load_config():
    """Read credentials from the environment (.env is loaded if present). Never prints values."""
    load_dotenv()
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        sys.exit("Missing environment variables: " + ", ".join(missing))
    cfg = {name: os.environ[name] for name in REQUIRED_ENV}
    if "YOUR_REDDIT_USERNAME" in cfg["REDDIT_USER_AGENT"]:
        sys.exit("REDDIT_USER_AGENT still contains the YOUR_REDDIT_USERNAME placeholder.")
    sub = cfg["TARGET_SUBREDDIT"]
    if not re.fullmatch(r"[A-Za-z0-9_]{2,21}", sub) or sub.lower() in {"all", "popular", "mod", "friends"}:
        sys.exit("TARGET_SUBREDDIT must be a single subreddit name without 'r/'.")
    return cfg


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config()
    dry_run = os.environ.get("DRY_RUN") == "1"
    reddit = praw.Reddit(
        client_id=cfg["REDDIT_CLIENT_ID"],
        client_secret=cfg["REDDIT_CLIENT_SECRET"],
        user_agent=cfg["REDDIT_USER_AGENT"],
        username=cfg["REDDIT_USERNAME"],
        password=cfg["REDDIT_PASSWORD"],
    )
    if with_backoff(reddit.user.me) is None:
        sys.exit("Login failed: Reddit returned no authenticated user.")
    subreddit = reddit.subreddit(cfg["TARGET_SUBREDDIT"])
    domains = load_blacklist()
    mods = {m.name.lower() for m in with_backoff(lambda: list(subreddit.moderator()))}
    started = time.time() - 120  # ignore older posts, including after a reconnect
    log.info("watching /new (dry_run=%s, blocked_domains=%d)", dry_run, len(domains))

    delay = 1
    while True:
        try:
            for submission in subreddit.stream.submissions():
                delay = 1
                if submission.created_utc < started:
                    continue
                try:
                    handle(submission, domains, mods, cfg["TARGET_SUBREDDIT"], dry_run)
                except (PrawcoreException, RedditAPIException) as exc:
                    log.error("could not moderate post=%s (%s)", submission.id, type(exc).__name__)
        except (TooManyRequests, ServerError, RequestException) as exc:
            wait = delay + random.uniform(0, 1)
            log.warning("stream interrupted (%s); reconnecting in %.0fs", type(exc).__name__, wait)
            time.sleep(wait)
            delay = min(delay * 2, MAX_DELAY)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
