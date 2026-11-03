from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from praw.exceptions import RedditAPIException, RedditErrorItem
from prawcore.exceptions import TooManyRequests

import main


def post(title="[Q] hello", url="https://reddit.com/x", is_self=True, author="alice", **extra):
    return MagicMock(
        title=title, url=url, is_self=is_self, id="abc",
        author=SimpleNamespace(name=author), approved_by=None, removed_by_category=None, **extra,
    )


def too_many():
    return TooManyRequests(Mock(headers={}, text="", status_code=429))


# --- rules -------------------------------------------------------------

@pytest.mark.parametrize("title", ["[Question] How do I?", "[A]x", "[Tag]   spaced"])
def test_good_titles(title):
    assert main.title_ok(title)


@pytest.mark.parametrize("title", ["No tag here", "[] empty", "[Tag]", "x [Tag] late", ""])
def test_bad_titles(title):
    assert not main.title_ok(title)


def test_blacklist_matches_domain_and_subdomains_only():
    domains = {"spam.com"}
    assert main.blacklisted("http://spam.com/a", domains)
    assert main.blacklisted("https://WWW.Spam.com/a", domains)
    assert main.blacklisted("https://spam.com./a", domains)
    assert not main.blacklisted("https://notspam.com/a", domains)
    assert not main.blacklisted("https://spam.com.evil.org/a", domains)
    assert not main.blacklisted("not a url", domains)


def test_load_blacklist_ignores_comments_and_blanks(tmp_path):
    f = tmp_path / "b.txt"
    f.write_text("# header\nSpam.com  # inline\n\n  bad.org\n", encoding="utf-8")
    assert main.load_blacklist(f) == {"spam.com", "bad.org"}
    assert main.load_blacklist(tmp_path / "missing.txt") == set()


def test_violation_order_and_self_posts():
    assert main.violation(post(title="no tag"), set()) == "title"
    assert main.violation(post(url="http://spam.com", is_self=False), {"spam.com"}) == "domain"
    assert main.violation(post(url="http://spam.com", is_self=True), {"spam.com"}) is None
    assert main.violation(post(), {"spam.com"}) is None


# --- backoff -----------------------------------------------------------

def test_backoff_retries_then_succeeds():
    calls, sleeps = [], []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise too_many()
        return "ok"

    assert main.with_backoff(flaky, sleep=sleeps.append) == "ok"
    assert len(sleeps) == 2 and sleeps[0] < sleeps[1]  # delays grow exponentially


def test_backoff_gives_up_after_max_retries():
    sleeps = []
    with pytest.raises(TooManyRequests):
        main.with_backoff(lambda: (_ for _ in ()).throw(too_many()), sleep=sleeps.append)
    assert len(sleeps) == main.MAX_RETRIES


def test_backoff_does_not_retry_other_api_errors():
    sleeps = []

    def boom():
        raise RedditAPIException([RedditErrorItem("FORBIDDEN", message="nope")])

    with pytest.raises(RedditAPIException):
        main.with_backoff(boom, sleep=sleeps.append)
    assert sleeps == []


def test_backoff_retries_api_ratelimit():
    n = []

    def limited():
        n.append(1)
        if len(n) == 1:
            raise RedditAPIException([RedditErrorItem("RATELIMIT", message="slow down")])
        return "ok"

    assert main.with_backoff(limited, sleep=lambda s: None) == "ok"


# --- handle ------------------------------------------------------------

def test_handle_removes_and_comments_without_echoing_post_text():
    s = post(title="evil [click](http://x) title")
    main.handle(s, set(), set(), "testsub")
    s.mod.remove.assert_called_once()
    body = s.reply.call_args.args[0]
    assert "evil" not in body and "click" not in body and "r/testsub" in body


def test_handle_dry_run_does_nothing():
    s = post(title="no tag")
    main.handle(s, set(), set(), "testsub", dry_run=True)
    s.mod.remove.assert_not_called()
    s.reply.assert_not_called()


def test_handle_skips_mods_deleted_approved_and_removed():
    mod_post = post(title="no tag", author="Boss")
    main.handle(mod_post, set(), {"boss"}, "s")
    deleted = post(title="no tag")
    deleted.author = None
    main.handle(deleted, set(), set(), "s")
    approved = post(title="no tag")
    approved.approved_by = "somemod"
    main.handle(approved, set(), set(), "s")
    removed = post(title="no tag")
    removed.removed_by_category = "moderator"
    main.handle(removed, set(), set(), "s")
    for s in (mod_post, deleted, approved, removed):
        s.mod.remove.assert_not_called()


def test_handle_leaves_compliant_posts_alone():
    s = post()
    main.handle(s, {"spam.com"}, set(), "s")
    s.mod.remove.assert_not_called()


# --- config ------------------------------------------------------------

@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(main, "load_dotenv", lambda: None)
    values = dict(
        REDDIT_CLIENT_ID="id", REDDIT_CLIENT_SECRET="secret",
        REDDIT_USER_AGENT="script:com.example.modbot:v1.0 (by /u/someone)",
        REDDIT_USERNAME="bot", REDDIT_PASSWORD="pw", TARGET_SUBREDDIT="testsub",
    )
    for k, v in values.items():
        monkeypatch.setenv(k, v)
    return monkeypatch


def test_config_ok(env):
    assert main.load_config()["TARGET_SUBREDDIT"] == "testsub"


def test_config_missing_var_names_it_but_never_prints_values(env, capsys):
    env.delenv("REDDIT_PASSWORD")
    with pytest.raises(SystemExit) as exc:
        main.load_config()
    assert "REDDIT_PASSWORD" in str(exc.value) and "secret" not in str(exc.value)


@pytest.mark.parametrize("sub", ["all", "r/x", "a+b", "x"])
def test_config_rejects_bad_subreddit(env, sub):
    env.setenv("TARGET_SUBREDDIT", sub)
    with pytest.raises(SystemExit):
        main.load_config()


def test_config_rejects_placeholder_user_agent(env):
    env.setenv("REDDIT_USER_AGENT", "script:com.example.modbot:v1.0 (by /u/YOUR_REDDIT_USERNAME)")
    with pytest.raises(SystemExit):
        main.load_config()
