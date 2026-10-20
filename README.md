# Day Zero

Vue 3·FastAPI 기반의 가상 협업 평가 플랫폼입니다. 공통 사용자 흐름과 시나리오 3종을 구현했습니다. v2 리포트는 활동 근거와 함께 최종 결과물 검토·역량별 AI 평가를 제공합니다. 점수·채용 판정·성격 단정은 생성하지 않습니다.

- 전체 설계: [AGENT.md](AGENT.md)
- 실행 방법: [docs/development.md](docs/development.md)
- AI 검토 기준·처리 범위: [docs/report-review.md](docs/report-review.md)
- 시나리오 시간표·자료·리포트 범위: [docs/scenarios-v1.md](docs/scenarios-v1.md)
- 구현·검증 현황: [docs/implementation-status.md](docs/implementation-status.md)
- 제품 기획: [PRD.md](PRD.md). 최신 사용자 합의는 루트 설계가 우선하며, 기존 시나리오 상세는 현재 사용하지 않습니다.

## 로컬 시작

Python 3.11 이상, Node.js 22.12 이상, uv, Docker Compose가 필요합니다.

```sh
uv sync --python 3.11
npm --prefix web ci
uv run python -m infra.configure
docker compose --env-file .env -f infra/compose.yaml up -d postgres rabbitmq
uv run --env-file .env python -m infra.dev --fixtures
```

[http://127.0.0.1:5173](http://127.0.0.1:5173)에서 기업·개인 계정을 각각 만들어 사용합니다. `--fixtures`는 전체 흐름 확인용 임시 설정만 불러오며, 실제 시나리오나 분석 결과가 아닙니다. 일반 실행에서는 해당 옵션을 빼세요.

기업 계정으로 역량 3~5개 → 지원하는 시나리오 → 프로젝트 생성 → 지원자 이메일 초대 → 로그아웃 → 해당 이메일로 개인 가입 → 나의 평가 → 수락·응시·제출 순서로 확인할 수 있습니다. 초대 링크는 직접 전달하며 이메일 자동 발송은 하지 않습니다.

AI 응답에는 서버 환경의 `OPENAI_API_KEY`가 필요합니다. 모델은 `gpt-4o-mini`로 고정되어 있습니다. 키가 없으면 입력 기록은 저장하고 AI 연결 불가를 안내합니다. 관찰 규칙이 없는 실행의 리포트는 `분석 미설정`입니다.

```sh
uv run pytest -q
npm --prefix web run build
```
