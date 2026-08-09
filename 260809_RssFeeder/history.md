# History

## 2026-08-09 (4)
403(봇 차단)이 확인된 언론사 도메인은 본문 요청 자체를 건너뛰도록 처리함.

**계기**
직전(3) 배포에서 상위 50건 중 news1.kr, einnews.com 두 곳이 매번 403으로 실패하는
것을 확인함 — 실패해도 요약으로 폴백되긴 하지만 매 실행마다 같은 요청을 반복하고
로그에 경고가 계속 쌓임.

**변경 내용**
- `config/topics.yaml`에 `blocked_domains` 목록 추가 (기본값: `news1.kr`, `einnews.com`).
  실행 로그에 403 경고가 반복되는 도메인을 여기 추가하면 됨(코드 수정 불필요).
- `is_blocked_domain(url, blocked_domains)`: URL의 호스트(www. 접두사 무시)가 목록에
  있는지 확인.
- `enrich_item_content`: 링크를 해석한 뒤 차단 도메인이면 `fetch_article_content`
  호출 자체를 생략. 해석된 URL은 성공/차단 여부와 무관하게 `item["resolved_link"]`에
  캐시해 두어, 다음 실행부터는 링크 재해석(Google 호출)도 건너뜀.
- `process_topic`: 로그에 "차단 도메인 건너뜀 N건"을 추가해 상태를 바로 확인 가능하게 함.
- 로컬 검증: 모킹으로 (1) 차단 도메인에 대해 `fetch_article_content`가 전혀 호출되지
  않는지, (2) `resolved_link`가 캐시되어 재사용되는지 확인. 실제 `data/digital-health.json`은
  테스트 중 일시적으로 변경됐다가 `git checkout`으로 원복함(커밋 전 실수로 섞여
  들어가지 않도록 주의).

## 2026-08-09 (3)
피드 항목에 기사 요약 대신(정확히는 요약 + 가능하면) 본문 전체를 채우는 기능을 추가함.

**변경 내용**
- `googlenewsdecoder`, `trafilatura` 의존성 추가.
- `resolve_article_url`: Google 뉴스 리다이렉트 링크를 실제 언론사 URL로 해석.
- `fetch_article_content`: 해석된 URL의 HTML을 받아 trafilatura로 본문만 추출
  (최대 6000자, 초과 시 `[…]`로 자름).
- `enrich_item_content`: 위 두 함수를 조합해 캐시 항목에 `content` 필드를 채움. 실패하면
  아무것도 하지 않고 다음 실행에서 재시도(요약은 항상 유지되므로 사용자 입장에서는
  실패해도 빈 피드가 아니라 요약만 있는 상태로 보임).
- `process_topic`: 실행 시간을 bounded하게 유지하려고 캐시 전체(최대 200건)가 아니라
  **피드에 실제로 노출되는 상위 `FEED_MAX_ITEMS`(50)건**에 대해서만 본문을 채우고,
  이미 채워진 항목은 건너뜀.
- `generate_feed_xml`: `content`가 있으면 RSS `content:encoded`(CDATA)로 출력, `description`은
  기존 요약을 그대로 유지.
- 로컬 검증: 샌드박스에서 `news.google.com`/실제 언론사 접근이 막혀 있어, (1) 합성 HTML로
  trafilatura 추출 자체를 검증하고 (2) `resolve_article_url`/`fetch_article_content`를
  모킹해 `process_topic` → `generate_feed_xml` 전체 배관과 실패 시 요약 폴백 동작을
  단위 테스트로 확인함. 실제 Google 뉴스 링크 해석/본문 추출은 다음 GitHub Actions
  실행에서 최종 확인 필요.

**알아둘 점**
- RSS `<link>`는 여전히 Google 리다이렉트 링크. 해석된 원문 URL은 캐시의
  `resolved_link` 필드에만 저장됨.
- 언론사 봇 차단/페이월/JS 렌더링 사이트는 본문 추출이 실패할 수 있음 — 이 경우
  요약만 노출되고 다음 실행에서 다시 시도함.

## 2026-08-09 (2)
`comsyfan-coder/app` 저장소에서 `comsyfan-coder/web` 저장소로 프로그램을 이전함. Pages 배포
URL이 저장소명에 따라 달라지므로 `config/topics.yaml`의 `pages_base_url`과 `readme.md`의
예시 URL을 `.../app/` -> `.../web/`으로 수정하고, 워크플로의 push 트리거 브랜치도
`master` -> `main`(web 저장소 기본 브랜치)으로 맞춤. 그 외 로직/구조는 변경 없음.

## 2026-08-09 (1)
최초 구현(MVP). "디지털헬스 동향" 주제 하나로 시작하는 RSS 피더 서비스를 만듦.

**현재 상태 요약**
- `config/topics.yaml`에 주제(디지털헬스 동향)와 Google 뉴스 검색 소스(한글/영문 각 1개) 정의.
- `scripts/fetch_feeds.py`: Google 뉴스 RSS 수집 -> URL 해시 기반 중복 제거 -> `data/<slug>.json`
  캐시 갱신 -> feedgen으로 `docs/feeds/<slug>.xml` 생성 -> `docs/index.html` 목록 페이지 생성.
- 저장소 루트 `.github/workflows/rss-feeder-digital-health.yml`: 6시간마다 cron 실행,
  결과를 자동 커밋하고 GitHub Pages(Actions 배포)로 `docs/`를 서빙.
- 로컬 검증: 샌드박스 네트워크 정책상 `news.google.com` 접근이 막혀 실제 수집은 GitHub Actions
  러너에서 처음 실행될 때 확인 필요. 대신 feedparser 샘플 XML을 넣어 dedup/캐시/feedgen 출력
  파이프라인 자체는 로컬에서 단위 테스트로 검증함.
- `data/digital-health.json`, `docs/feeds/digital-health.xml`, `docs/index.html`은 빈 상태로
  최초 커밋에 포함(구조 확인 및 Pages 활성화 전 배포 가능하도록). 첫 워크플로 실행 시 실제
  기사로 채워짐.

**다음에 할 일 후보**
- 새 주제/사이트 추가 (`config/topics.yaml`에 항목 추가).
- 필요 시 네이버 검색 API, 특정 언론사 RSS 등 소스 타입 확장 (`build_source_url`).
- Google 뉴스 리다이렉트 링크를 원문 URL로 풀어주는 로직 검토.
