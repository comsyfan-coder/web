# History

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
