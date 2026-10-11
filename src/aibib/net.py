"""Polite HTTP: one request at a time across processes, >= 3 s apart, cached on disk."""

import fcntl
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import __version__

USER_AGENT = (f"AI-Bib-Helper/{__version__} "
              "(+https://github.com/xzAscC/AI-Bib-Helper; BibTeX lookup for citations)")
INTERVAL = 3.0
CACHE = Path(os.environ.get("AIBIB_CACHE") or Path.home() / ".cache" / "aibib")
refresh = False  # set by the CLI's --refresh


class FetchError(RuntimeError):
    pass


class RateLimited(FetchError):
    """429/503: the caller must stop, not retry."""


def fetch(url, data=None, headers=None, check=None, interval=INTERVAL):
    """GET (or POST when data is given) url; return (final_url, text).

    check(text) may raise to reject a response (e.g. a bot-check page);
    rejected responses are not cached. interval honours a site's Crawl-delay.
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    tag = f"{url}\n{data or ''}\n{sorted((headers or {}).items())}"
    path = CACHE / (hashlib.sha256(tag.encode()).hexdigest() + ".json")
    if path.exists() and not refresh:
        hit = json.loads(path.read_text())
        return hit["url"], hit["text"]

    req = urllib.request.Request(url, data=data.encode() if data else None,
                                 headers={"User-Agent": USER_AGENT, **(headers or {})})
    # The lock file serializes requests across concurrent aibib processes;
    # its mtime records when the last request started.
    with open(CACHE / ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        wait = os.fstat(lock.fileno()).st_mtime + interval - time.time()
        if wait > 0:
            time.sleep(wait)
        os.utime(lock.fileno())
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                final, text = r.geturl(), r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (429, 503):
                raise RateLimited(f"{e.code} from {url}; stop and retry later with a longer interval")
            raise FetchError(f"HTTP {e.code} from {url}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            raise FetchError(f"{url}: {getattr(e, 'reason', e)}") from None

    if "<title>Making sure you&#39;re not a bot!</title>" in text:
        raise FetchError(f"{url} answered with a bot check (Anubis); it does not allow scripts")
    if check:
        check(text)
    path.write_text(json.dumps({"url": final, "text": text}))
    return final, text
