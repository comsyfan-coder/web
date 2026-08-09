#!/usr/bin/env python3
"""
RSS 피더 서비스 - 주제별 뉴스를 모아 정적 RSS XML 파일로 생성한다.

흐름:
  1) config/topics.yaml 에 정의된 각 주제(topic)를 순회한다.
  2) 주제에 딸린 source(현재는 Google 뉴스 검색)마다 RSS를 가져와 파싱한다.
  3) data/<slug>.json 캐시와 URL 해시로 비교해 이미 본 기사를 걸러낸다.
  4) 피드에 실릴 상위 기사(FEED_MAX_ITEMS)는 Google 뉴스 리다이렉트 링크를 실제
     언론사 URL로 해석한 뒤, 본문을 가져와 요약이 아닌 본문 전체(가능한 만큼)를
     content:encoded 로 채운다. 실패하면 기존 요약으로 자동 폴백한다.
  5) 새/기존 기사를 합쳐 캐시를 갱신하고, feedgen으로 docs/feeds/<slug>.xml 을 만든다.
  6) 전체 주제를 모아 docs/index.html(피드 목록 페이지)도 갱신한다.

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
from urllib.parse import quote, urlparse

import feedparser
import requests
import trafilatura
import yaml
from feedgen.feed import FeedGenerator
from googlenewsdecoder import gnewsdecoder

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

ARTICLE_FETCH_TIMEOUT = 12  # 원문 기사 페이지 요청 타임아웃(초)
ARTICLE_CONTENT_MAX_CHARS = 6000  # content:encoded 에 담을 본문 최대 길이
ARTICLE_FETCH_DELAY = 0.3  # 언론사/구글 서버 부담을 줄이기 위한 요청 간 딜레이(초)


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


def resolve_article_url(google_link: str) -> str | None:
    """Google 뉴스 리다이렉트 링크를 실제 언론사 기사 URL로 해석한다. 실패 시 None."""
    try:
        result = gnewsdecoder(google_link, interval=ARTICLE_FETCH_DELAY)
    except Exception as exc:  # googlenewsdecoder는 다양한 예외를 던질 수 있음
        print(f"    [경고] 링크 해석 실패: {exc}")
        return None
    if result.get("status") and result.get("decoded_url"):
        return result["decoded_url"]
    return None


def is_blocked_domain(url: str, blocked_domains: set[str]) -> bool:
    """url의 호스트가 차단 목록에 있으면 True. www. 접두사는 무시하고 비교한다."""
    if not blocked_domains:
        return False
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host in blocked_domains


def fetch_article_content(url: str) -> str | None:
    """기사 원문 페이지에서 본문만 추출한다. 실패하면 None을 돌려준다."""
    try:
        response = requests.get(url, headers=REQUEST_HEADERS, timeout=ARTICLE_FETCH_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"    [경고] 기사 본문 요청 실패 ({url}): {exc}")
        return None

    text = trafilatura.extract(
        response.text,
        url=url,
        include_comments=False,
        favor_precision=True,
    )
    if not text:
        return None

    text = text.strip()
    if len(text) > ARTICLE_CONTENT_MAX_CHARS:
        text = text[:ARTICLE_CONTENT_MAX_CHARS].rstrip() + " […]"
    return text


def enrich_item_content(item: dict, blocked_domains: set[str]) -> bool:
    """item에 본문(content)을 채운다. 이미 있거나 실패/차단 도메인이면 아무것도 하지 않는다."""
    if item.get("content"):
        return False

    # 링크 해석 결과는 캐시에 남겨두고 재사용한다 - 차단 도메인이든 아니든
    # 매 실행마다 Google 링크 해석 요청을 다시 보낼 필요는 없다.
    real_url = item.get("resolved_link") or resolve_article_url(item["link"])
    if not real_url:
        return False
    item["resolved_link"] = real_url

    if is_blocked_domain(real_url, blocked_domains):
        return False

    content = fetch_article_content(real_url)
    time.sleep(ARTICLE_FETCH_DELAY)
    if not content:
        return False

    item["content"] = content
    return True


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


def process_topic(topic: dict, blocked_domains: set[str]) -> list[dict]:
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

    # 피드에 실제로 노출되는 상위 기사만 본문을 채운다(전체 캐시를 매번 다시
    # 긁으면 실행 시간이 너무 길어지므로). 이미 본문이 있는 기사는 건너뛴다.
    enriched = 0
    blocked = 0
    for item in combined[:FEED_MAX_ITEMS]:
        if item.get("content"):
            continue
        if enrich_item_content(item, blocked_domains):
            enriched += 1
        elif item.get("resolved_link") and is_blocked_domain(item["resolved_link"], blocked_domains):
            blocked += 1

    save_cache(slug, combined)
    print(
        f"  {slug}: 신규 {len(new_items)}건, 본문 보강 {enriched}건, "
        f"차단 도메인 건너뜀 {blocked}건, 전체 캐시 {len(combined)}건"
    )
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
        if item.get("content"):
            fe.content(item["content"], type="CDATA")
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
    blocked_domains = {d.lower() for d in config.get("blocked_domains", [])}

    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] RSS 피더 갱신 시작 ({len(topics)}개 주제)")
    for topic in topics:
        items = process_topic(topic, blocked_domains)
        generate_feed_xml(topic, items, pages_base_url)

    generate_index_html(topics, pages_base_url)
    print("완료")


if __name__ == "__main__":
    main()
