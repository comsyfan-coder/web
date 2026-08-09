#!/usr/bin/env python3
"""
RSS 피더 서비스 - 주제별 뉴스를 모아 정적 RSS XML 파일로 생성한다.

흐름:
  1) config/topics.yaml 에 정의된 각 주제(topic)를 순회한다.
  2) 주제에 딸린 source(현재는 Google 뉴스 검색)마다 RSS를 가져와 파싱한다.
  3) data/<slug>.json 캐시와 URL 해시로 비교해 이미 본 기사를 걸러낸다.
  4) 새/기존 기사를 합쳐 캐시를 갱신하고, feedgen으로 docs/feeds/<slug>.xml 을 만든다.
  5) 전체 주제를 모아 docs/index.html(피드 목록 페이지)도 갱신한다.

GitHub Actions 스케줄러가 이 스크립트를 주기 실행하고, 변경된 data/·docs/ 를
커밋 -> GitHub Pages 로 배포하는 구조를 전제로 한다.
"""

from __future__ import annotations

import calendar
import hashlib
import html
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import feedparser
import requests
import yaml
from feedgen.feed import FeedGenerator

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config" / "topics.yaml"
DATA_DIR = BASE_DIR / "data"
DOCS_DIR = BASE_DIR / "docs"
FEEDS_DIR = DOCS_DIR / "feeds"

REQUEST_TIMEOUT = 15
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

CACHE_MAX_ITEMS = 200  # data/<slug>.json 에 보관하는 최대 기사 수 (과거 이력)
FEED_MAX_ITEMS = 50  # docs/feeds/<slug>.xml 에 담는 최대 기사 수


def load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def google_news_url(source: dict) -> str:
    query = quote(source["query"])
    hl = source.get("hl", "ko")
    gl = source.get("gl", "KR")
    ceid = source.get("ceid", "KR:ko")
    return f"https://news.google.com/rss/search?q={query}&hl={hl}&gl={gl}&ceid={ceid}"


def build_source_url(source: dict) -> str:
    source_type = source.get("type")
    if source_type == "google_news":
        return google_news_url(source)
    raise ValueError(f"지원하지 않는 source type: {source_type!r}")


def url_hash(url: str) -> str:
    # 추적용 쿼리스트링 차이로 같은 기사가 다르게 잡히는 것을 줄이기 위해
    # 경로까지만 해시한다.
    normalized = url.split("?", 1)[0].strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def parse_published(entry) -> datetime:
    if getattr(entry, "published_parsed", None):
        return datetime.fromtimestamp(
            calendar.timegm(entry.published_parsed), tz=timezone.utc
        )
    return datetime.now(tz=timezone.utc)


def fetch_source_items(source: dict, source_label: str) -> list[dict]:
    url = build_source_url(source)
    response = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    parsed = feedparser.parse(response.content)

    items = []
    for entry in parsed.entries:
        link = entry.get("link", "").strip()
        title = html.unescape(entry.get("title", "").strip())
        if not link or not title:
            continue
        items.append(
            {
                "hash": url_hash(link),
                "title": title,
                "link": link,
                "published": parse_published(entry).isoformat(),
                "source": source_label,
                "summary": html.unescape(entry.get("summary", "")),
            }
        )
    return items


def load_cache(slug: str) -> list[dict]:
    cache_path = DATA_DIR / f"{slug}.json"
    if not cache_path.exists():
        return []
    with cache_path.open("r", encoding="utf-8") as f:
        return json.load(f).get("items", [])


def save_cache(slug: str, items: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = DATA_DIR / f"{slug}.json"
    payload = {
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
        "items": items,
    }
    with cache_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def process_topic(topic: dict) -> list[dict]:
    slug = topic["slug"]
    existing_items = load_cache(slug)
    seen_hashes = {item["hash"] for item in existing_items}

    new_items = []
    for source in topic.get("sources", []):
        label = f"Google 뉴스 ({source.get('hl', 'ko')})"
        try:
            fetched = fetch_source_items(source, label)
        except (requests.RequestException, ValueError) as exc:
            print(f"  [경고] {slug} 소스 수집 실패 ({label}): {exc}")
            continue

        for item in fetched:
            if item["hash"] in seen_hashes:
                continue
            seen_hashes.add(item["hash"])
            new_items.append(item)

    combined = new_items + existing_items
    combined.sort(key=lambda i: i["published"], reverse=True)
    combined = combined[:CACHE_MAX_ITEMS]

    save_cache(slug, combined)
    print(f"  {slug}: 신규 {len(new_items)}건, 전체 캐시 {len(combined)}건")
    return combined


def generate_feed_xml(topic: dict, items: list[dict], pages_base_url: str) -> None:
    FEEDS_DIR.mkdir(parents=True, exist_ok=True)
    slug = topic["slug"]

    fg = FeedGenerator()
    fg.title(topic["title"])
    fg.link(href=f"{pages_base_url}feeds/{slug}.xml", rel="self")
    fg.link(href=topic.get("link", "https://news.google.com/"), rel="alternate")
    fg.description(topic["description"])
    fg.language("ko")
    fg.lastBuildDate(datetime.now(tz=timezone.utc))

    for item in items[:FEED_MAX_ITEMS]:
        fe = fg.add_entry()
        fe.id(item["link"])
        fe.title(item["title"])
        fe.link(href=item["link"])
        fe.description(item.get("summary", ""))
        fe.pubDate(item["published"])
        if item.get("source"):
            fe.category(term=item["source"])

    fg.rss_file(str(FEEDS_DIR / f"{slug}.xml"))


def generate_index_html(topics: list[dict], pages_base_url: str) -> None:
    rows = []
    for topic in topics:
        slug = topic["slug"]
        feed_url = f"{pages_base_url}feeds/{slug}.xml"
        rows.append(
            f"""
        <li>
          <h2>{html.escape(topic['title'])}</h2>
          <p>{html.escape(topic['description'])}</p>
          <a href="feeds/{slug}.xml">{feed_url}</a>
        </li>"""
        )

    updated_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    content = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>RSS 피더 - 주제별 뉴스 모음</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
</head>
<body>
  <h1>RSS 피더</h1>
  <p>마지막 갱신: {updated_at}</p>
  <ul>{''.join(rows)}
  </ul>
</body>
</html>
"""
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (DOCS_DIR / "index.html").write_text(content, encoding="utf-8")


def main() -> None:
    config = load_config()
    pages_base_url = config["pages_base_url"]
    topics = config["topics"]

    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] RSS 피더 갱신 시작 ({len(topics)}개 주제)")
    for topic in topics:
        items = process_topic(topic)
        generate_feed_xml(topic, items, pages_base_url)

    generate_index_html(topics, pages_base_url)
    print("완료")


if __name__ == "__main__":
    main()
