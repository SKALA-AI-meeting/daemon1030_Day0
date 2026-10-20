# 개발 환경과 실행

## 현재 실행 방식

로컬에서 PostgreSQL·RabbitMQ는 Docker로, Vue·Python 서버는 `infra.dev` 프로세스 관리자로 실행합니다. 구성은 web, Gateway, Agent, business-mcp, rag-mcp, Outbox 전달기, report-worker입니다.

1. 루트에서 `uv sync --python 3.11`, `npm --prefix web ci`를 실행합니다.
2. `uv run python -m infra.configure`로 .env를 만듭니다. 기존 파일은 덮어쓰지 않습니다.
3. `docker compose --env-file .env -f infra/compose.yaml up -d postgres rabbitmq`로 개발 DB·큐를 시작합니다.
4. `uv run --env-file .env python -m infra.dev --fixtures`를 실행합니다.
5. 웹은 `http://127.0.0.1:5173`, Gateway 문서는 `http://127.0.0.1:8000/docs`입니다.

`infra.dev`는 마이그레이션과 catalog 등록을 먼저 실행하고 자기 자식 프로세스만 관리합니다. Ctrl+C는 앱 프로세스를 종료합니다. DB·큐 데이터는 유지됩니다. DB·큐를 중지하려면 `docker compose --env-file .env -f infra/compose.yaml stop`을 사용합니다. 데이터 삭제 명령은 실행하지 않습니다.

## 환경 변수

| 변수 | 용도 |
|---|---|
| DATABASE_URL | PostgreSQL SQLAlchemy URL |
| AUTH_SECRET | 서비스 범위 자격의 서명 키. configure가 임의 값을 생성 |
| DAYZERO_DEVELOPMENT | 개발 fixture·비보안 HTTP 로컬 쿠키·임시 임베딩 활성화. 일반 실행은 false |
| AGENT_URL / GATEWAY_URL | 서버 내부 HTTP 주소 |
| BUSINESS_MCP_URL / RAG_MCP_URL | 실제 Streamable HTTP MCP 엔드포인트 |
| RABBITMQ_URL | Outbox·워커 연결 |
| OPENAI_API_KEY | gpt-4o-mini 호출. 브라우저에 전달하지 않음 |
| AGENT_CHECKPOINT_PATH | 로컬 Agent 영속 SQLite 경로. 업무 데이터는 PostgreSQL |
| EMBEDDING_URL / EMBEDDING_MODEL_ID | Text Embeddings Inference 방식 `/embed` 엔드포인트 전체 URL과 모델 버전 ID |

환경 파일·키·데이터는 Git에서 제외합니다. 외부 추적에 전체 대화를 자동 전송하지 않습니다.

## 개발 fixture와 실제 평가의 구분

- S01~S03 이름 초안과 별도로 S01-v1~S03-v1이 등록됩니다. 실제 과제·자료·정시 직원 개입·관찰 근거 규칙은 [시나리오 명세](scenarios-v1.md)를 따릅니다. 일반 실행도 해당 버전을 선택할 수 있습니다.
- 전체 기능 확인 설정은 `tests/fixtures/workspace.json` 하나입니다. 개발 모드에서 `--fixtures`를 명시해야 등록되며 화면에도 실제 평가가 아님을 표시합니다.
- 개발 설정에는 빈 사건 목록과 일반 작업 영역만 있습니다. 관찰 규칙이 없으므로 워커는 `not_configured`를 저장합니다.
- 개발 fixture의 지원 역량 매핑은 제품 시나리오의 지원 범위를 정의하지 않습니다. 개발 모드를 끄면 DB에 fixture가 남아 있어도 조회·선택을 거절합니다.
- `.env.example`의 DAYZERO_DEVELOPMENT=true는 로컬 개발용입니다. 일반 실행에 개발 fixture를 발행하지 않습니다.

## 연결 종료·시간·제출

사용자가 확정한 정책은 연결 종료 감지 시 일시정지, 언제든 최종 제출 허용입니다. WebSocket 종료를 받으면 즉시 멈추고, 감지되지 않은 단절은 5초 ping·15초 유예 기준으로 판단합니다. 재접속은 같은 개인 계정의 세션에만 허용됩니다. 새 연결이 생기면 이전 연결은 4409로 닫아 여러 탭이 연결을 반복 교체하지 않게 합니다.

최종 제출 전 자동 저장을 완료하고, 제출·최종 스냅샷·Outbox를 한 트랜잭션으로 확정합니다. 원본을 바꾸지 않고 리포트를 재처리할 수 있습니다.

## MCP·검색·문서 승인

business-mcp는 9101/mcp, rag-mcp는 9102/mcp에서 실행됩니다. Agent가 역할별 자격으로 도구를 조회·호출하며 Gateway 내부 API가 업무 권한을 재검사합니다.

기업의 사내 문서 화면에서 등록 준비 → 관리자 내용·해시 확인 → 승인/거절을 수행합니다. Agent의 별도 LangGraph가 interrupt·체크포인트에서 대기하고, 승인 후 rag-mcp가 청크·벡터를 생성합니다. 인덱싱 성공 문서는 이후 생성한 프로젝트에만 고정되며 기존 평가에는 추가되지 않습니다.

개발 모드의 기본 임베딩은 검색 계약을 검증하기 위한 `fixture-hash-256-v1`입니다. 한국어 의미 검색 성능을 검증한 모델이 아닙니다. 일반 모드에서는 EMBEDDING_URL·EMBEDDING_MODEL_ID가 필요하며 임베딩을 임의 대체하지 않습니다. pgvector의 모델별 벡터 거리 검색을 사용합니다.

## 테스트

```sh
uv run pytest -q
uv run ruff check contracts services tests migrations infra
npm --prefix web run build
```

기본 테스트는 SQLite 격리 DB·합성 기록·모델 대역을 사용하며 유료 모델을 호출하지 않습니다. 실제 PostgreSQL·두 MCP 서버·승인·pgvector·RabbitMQ 통합 검증은 개발 스택을 실행한 상태에서 별도로 수행합니다.

```sh
DAYZERO_LIVE_TESTS=1 uv run --env-file .env pytest tests/integration/test_live_stack.py -q
```

이 검증은 명시적으로 켠 로컬 개발 DB에 고유 접두어 합성 계정·프로젝트·문서를 생성합니다. 실제 지원자 데이터로 실행하지 않습니다. MCP 프로토콜 테스트와 실제 모델 도구 선택 시연은 별개의 증거입니다.

## 계약·마이그레이션

`uv run python -m contracts.export`로 OpenAPI·시나리오·작업 큐·MCP 스키마의 생성본을 갱신합니다. API 입력·시나리오·큐 형식의 원본은 contracts의 Python 모델이며 MCP 도구의 서명과 생성 계약은 함께 검증합니다. JSON 생성본을 독립적으로 수정하지 않습니다.

DB 변경은 Alembic을 사용합니다. `uv run --env-file .env alembic -c migrations/alembic.ini upgrade head`로 적용합니다. 자동 DB 초기화·기존 데이터 삭제는 하지 않습니다.

## 전체 컨테이너 실행 구성

`infra/compose.yaml`의 app profile과 Dockerfile이 준비되어 있습니다. 로컬 프로세스 실행과 별도로 이미지 빌드·기동 검증 상태는 구현 현황 문서에 기록합니다.

```sh
docker compose --env-file .env -f infra/compose.yaml --profile app up --build -d
```

컨테이너 웹은 `http://127.0.0.1:8080`입니다. 기본 catalog에는 시나리오 3종 v1이 포함됩니다. 6개 역량 전체를 선택하면 지원 버전이 없어 결과가 없습니다. 로컬과 컨테이너 앱을 동시에 실행하면 같은 큐·DB를 공유하므로 하나의 실행 방식을 선택합니다.
