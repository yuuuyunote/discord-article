"""
Discord公式ブログ (https://discord.com/blog) の新着記事を検知し、
Discord Webhookへ通知するスクリプト。

- discord.com/blog にはRSS/Atomフィードが存在しないため、
  ブログ一覧ページをスクレイピングして記事スラッグを抽出する。
- 既知のスラッグは seen_posts.json に永続化し、
  未知のスラッグが出現したら個別記事ページのOGPメタ情報を取得して通知する。
- 初回実行時は通知を送らず、現在の記事一覧を「既読」として保存するだけにする。
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BLOG_URL = "https://discord.com/blog"
STATE_PATH = Path("seen_posts.json")
MAX_STATE_SIZE = 500  # 古いスラッグから間引いてファイル肥大化を防ぐ
REQUEST_TIMEOUT = 30
USER_AGENT = "Mozilla/5.0 (compatible; GuideBasePlus-BlogWatcher/1.0)"

WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")


def load_seen() -> set[str]:
    if STATE_PATH.exists():
        return set(json.loads(STATE_PATH.read_text(encoding="utf-8")))
    return set()


def save_seen(seen: set[str]) -> None:
    trimmed = sorted(seen)[-MAX_STATE_SIZE:]
    STATE_PATH.write_text(
        json.dumps(trimmed, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def fetch_blog_slugs() -> list[str]:
    res = requests.get(BLOG_URL, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")

    slugs: list[str] = []
    local_seen: set[str] = set()
    for a in soup.select("a[href^='/blog/']"):
        href = a.get("href", "")
        m = re.fullmatch(r"/blog/([a-z0-9-]+)", href)
        if not m:
            continue
        slug = m.group(1)
        if slug in local_seen:
            continue
        local_seen.add(slug)
        slugs.append(slug)
    return slugs


def fetch_post_meta(slug: str) -> dict:
    url = f"{BLOG_URL}/{slug}"
    post = {"url": url, "title": slug, "description": None, "image": None}
    try:
        res = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")

        def meta(prop: str) -> str | None:
            tag = soup.find("meta", attrs={"property": prop}) or soup.find(
                "meta", attrs={"name": prop}
            )
            content = tag.get("content") if tag else None
            return content.strip() if content else None

        post["title"] = meta("og:title") or (soup.title.string.strip() if soup.title else slug)
        post["description"] = meta("og:description")
        post["image"] = meta("og:image")
    except requests.RequestException as e:
        print(f"[warn] failed to fetch meta for {slug}: {e}", file=sys.stderr)
    return post


def notify_discord(post: dict) -> None:
    embed = {"title": post["title"], "url": post["url"], "color": 0x5865F2}
    if post.get("description"):
        embed["description"] = post["description"]
    if post.get("image"):
        embed["image"] = {"url": post["image"]}

    payload = {
        "content": "📰 Discord公式ブログに新着記事があります",
        "embeds": [embed],
    }
    res = requests.post(WEBHOOK_URL, json=payload, timeout=REQUEST_TIMEOUT)
    res.raise_for_status()


def main() -> int:
    if not WEBHOOK_URL:
        print("[error] DISCORD_WEBHOOK_URL is not set", file=sys.stderr)
        return 1

    seen = load_seen()
    current_slugs = fetch_blog_slugs()

    if not current_slugs:
        print("[warn] no slugs found on blog page; page structure may have changed", file=sys.stderr)
        return 1

    if not seen:
        # 初回実行: いきなり大量通知しないよう、現状を既読として保存するだけにする
        save_seen(set(current_slugs))
        print(f"Initialized state with {len(current_slugs)} existing posts. No notifications sent.")
        return 0

    new_slugs = [s for s in current_slugs if s not in seen]

    if not new_slugs:
        print("No new posts.")
        return 0

    # ページ上は新しい順に並んでいるため、古い方から順に通知する
    for slug in reversed(new_slugs):
        post = fetch_post_meta(slug)
        notify_discord(post)
        print(f"Notified: {post['url']}")

    seen.update(current_slugs)
    save_seen(seen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
