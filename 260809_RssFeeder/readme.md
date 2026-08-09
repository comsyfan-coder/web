# RSS 피더 (RssFeeder)

## 설명

관심 주제별 뉴스를 자동으로 모아 정적 RSS(XML) 피드로 만들어 GitHub Pages에 올리는 서비스입니다.
생성된 피드는 이 저장소에서 직접 서빙되며, 외부 RSS 리더기(Feedly, Inoreader 등)에 피드 URL을
등록해서 구독하는 용도로 사용합니다.

MVP는 **"디지털헬스 동향"** 한 개 주제로 시작하며, `config/topics.yaml`에 항목을 추가하는 것만으로
주제나 사이트를 계속 늘릴 수 있도록 설계했습니다(코드 수정 불필요).

## 아키텍쳐

### 동작 흐름

```
GitHub Actions (cron, 6시간마다)
  └─ scripts/fetch_feeds.py 실행
       1. config/topics.yaml 에서 주제 목록/검색어 로드
       2. 주제별 source(Google 뉴스 검색 RSS)를 요청·파싱
       3. data/<slug>.json 캐시와 URL 해시로 비교 -> 신규 기사만 추가
       4. 피드에 실릴 상위 기사(최근 50건)에 한해 Google 뉴스 링크를 실제
          언론사 URL로 해석하고, 본문을 가져와 최대한 채운다(실패 시 요약으로 폴백)
       5. 캐시(최근 200건 유지, 본문 포함)를 다시 저장
       6. feedgen으로 docs/feeds/<slug>.xml 재생성 (요약은 description,
          본문은 content:encoded)
       7. docs/index.html(피드 목록 페이지) 재생성
  └─ 변경된 data/, docs/ 를 저장소에 커밋 & 푸시
  └─ docs/ 를 GitHub Pages 아티팩트로 업로드 & 배포
```

### 폴더 구성

```
config/topics.yaml   - 주제/검색어/출처 정의 (신규 주제는 여기에 항목만 추가)
scripts/fetch_feeds.py - 수집 -> 중복 제거 -> 캐시 저장 -> RSS/인덱스 생성 전체 로직
data/<slug>.json     - 주제별 기사 캐시 (URL 해시로 중복 판단, 최근 200건)
docs/feeds/<slug>.xml - 실제 배포되는 RSS 피드 (GitHub Pages로 서빙)
docs/index.html      - 전체 피드 목록 페이지
requirements.txt     - Python 의존성
```

저장소 루트의 `.github/workflows/rss-feeder-digital-health.yml` 이 스케줄링/배포를 담당합니다.

### 중복 제거 방식

같은 주제에 검색어(source)를 여러 개 등록해도 동일 기사가 여러 번 잡힐 수 있습니다.
기사 링크에서 쿼리스트링을 제거한 뒤 SHA-256 해시를 구해 `data/<slug>.json` 캐시에 있는
해시와 비교하는 방식으로 중복을 걸러내고, 새 기사만 캐시에 추가합니다.

### 본문 삽입 방식

Google 뉴스 RSS가 주는 `summary`는 한두 줄짜리 짧은 요약뿐이라, 가능하면 기사 본문을
피드에 함께 실어 리더기에서 바로 읽을 수 있게 했습니다.

1. `resolve_article_url` — Google 뉴스가 감싼 리다이렉트 링크(`news.google.com/rss/articles/...`)를
   `googlenewsdecoder`로 실제 언론사 기사 URL로 해석합니다.
2. `fetch_article_content` — 해석된 URL을 요청해서 받은 HTML을 `trafilatura`로 파싱해
   광고/네비게이션/댓글 등을 제외한 본문만 추출합니다(`ARTICLE_CONTENT_MAX_CHARS`=6000자
   까지, 넘으면 잘라서 `[…]` 표시).
3. 성공하면 `item["content"]`에 저장되고, feedgen이 이를 RSS의 `content:encoded`
   (요약과 별개로 대부분의 리더기가 "본문"으로 인식하는 필드)에 채웁니다. 기존
   `description`(짧은 요약)은 그대로 유지됩니다.
4. 링크 해석이나 본문 요청·추출 중 하나라도 실패하면(언론사 봇 차단, 페이월, 타임아웃 등)
   조용히 실패하고 요약만 있는 상태로 남습니다 — 다음 실행 때 그 기사가 여전히 상위
   50건 안에 있으면 다시 시도합니다.
5. 실행 시간을 제한하기 위해 **피드에 실제로 노출되는 상위 `FEED_MAX_ITEMS`(50)건에
   대해서만** 본문을 채우며, 이미 본문이 있는 기사는 건너뜁니다. 그래서 최초 실행 이후에는
   새로 상위 50위 안에 들어온 기사만 추가로 요청하게 됩니다.
6. `config/topics.yaml`의 `blocked_domains`에 등록된 언론사 도메인은 매번 403으로
   실패하는 게 확인되면 **본문 요청 자체를 시도하지 않고** 곧바로 요약으로 넘어갑니다.
   해석된 URL(`resolved_link`)은 캐시에 저장해 두어, 차단 여부 판단에 링크를 다시
   해석할 필요도 없게 했습니다. 로그에 "[경고] 기사 본문 요청 실패 (403 ...)"가
   반복되는 도메인이 있으면 `blocked_domains`에 추가하면 됩니다.

## 기술스택

- **Python 3.11**
- **feedparser** — Google 뉴스 RSS 파싱
- **feedgen** — RSS 2.0 피드 생성 (요약: `description`, 본문: `content:encoded`)
- **requests** — HTTP 요청
- **PyYAML** — `topics.yaml` 설정 로드
- **googlenewsdecoder** — Google 뉴스 리다이렉트 링크를 실제 기사 URL로 해석
- **trafilatura** — 기사 HTML에서 본문만 추출(광고/네비게이션 제거)
- **GitHub Actions** — cron 스케줄링 및 자동 커밋/배포
- **GitHub Pages** — `docs/` 폴더 정적 서빙 (RSS XML 파일 호스팅)

## 사용법

### 로컬 실행

```bash
cd 260809_RssFeeder
pip install -r requirements.txt
python scripts/fetch_feeds.py
```

실행하면 `data/*.json`(캐시)과 `docs/feeds/*.xml`(피드), `docs/index.html`(목록 페이지)이 갱신됩니다.

### 새 주제/사이트 추가하기

`config/topics.yaml`에 아래 형식으로 항목을 추가하면 됩니다. 현재는 Google 뉴스 검색
(`type: google_news`)만 지원하며, 필요하면 `scripts/fetch_feeds.py`의 `build_source_url`에
새 source type(특정 사이트 RSS, 네이버 검색 API 등)을 추가해서 확장할 수 있습니다.

```yaml
topics:
  - slug: my-topic-slug
    title: "주제 이름"
    description: "주제 설명"
    link: "https://news.google.com/"
    sources:
      - type: google_news
        query: "검색어1 OR 검색어2"
        hl: ko
        gl: KR
        ceid: "KR:ko"
```

### GitHub Pages 활성화 (최초 1회, 저장소 설정)

1. GitHub 저장소 **Settings → Pages** 에서 Source를 **GitHub Actions**로 설정합니다.
2. `.github/workflows/rss-feeder-digital-health.yml` 워크플로가 `docs/` 폴더를
   Pages 사이트 루트로 배포합니다.
3. 배포 후 피드 주소는 `https://<owner>.github.io/<repo>/feeds/<slug>.xml` 형태가 됩니다.
   (예: `https://comsyfan-coder.github.io/web/feeds/digital-health.xml`)
4. 이 피드 URL을 원하는 RSS 리더기에 등록해서 구독하면 됩니다.

### 스케줄

기본은 6시간마다(UTC 0/6/12/18시) 자동 실행됩니다. 주기를 바꾸려면 워크플로 파일의
`cron` 값을 수정하세요. `workflow_dispatch`도 등록되어 있어 Actions 탭에서 수동 실행도
가능합니다.

## 비고 및 추가 내용

- Google 뉴스 검색 RSS는 별도 API 키 없이 사용할 수 있어 MVP에 채택했습니다. 이후 네이버
  검색 API나 특정 언론사/기관 RSS를 소스로 추가해 커버리지를 넓힐 수 있습니다.
- 캐시(`data/<slug>.json`)는 저장소에 함께 커밋되므로, GitHub Actions 러너가 매번 새로
  시작해도 "이전에 이미 내보낸 기사" 상태가 유지됩니다. 한 번 추출된 본문도 캐시에
  같이 저장되어 재요청하지 않습니다.
- RSS의 `<link>`는 여전히 Google 뉴스 리다이렉트 링크입니다(클릭하면 Google을 거쳐
  이동). 해석된 원문 URL은 `item["resolved_link"]`(캐시 JSON)에 별도로 남겨두며,
  필요하면 `<link>` 자체를 원문 URL로 바꾸도록 `generate_feed_xml`을 수정할 수 있습니다.
- 언론사가 스크래핑을 막아두었거나 자바스크립트 렌더링이 필요한 사이트는 본문 추출이
  실패할 수 있습니다(이 경우 요약만 노출). 본문 전문을 그대로 재배포하는 것은 저작권
  이슈가 있을 수 있어, 개인 구독용으로 사용하고 길이도 6000자로 제한해 두었습니다.
