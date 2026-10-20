# migrations — DB 스키마·버전·기본 데이터 구현 범위

기준: [루트 설계](../AGENT.md) 4·6·7·8장.

## 역할

PostgreSQL·pgvector 스키마의 변경 이력·관계·제약·인덱스·DB 역할을 관리한다. 마이그레이션 도구와 저장 스키마 명칭은 첫 구현 시 고정하고 실행 방법을 docs에 기록한다. 업무 처리는 서비스가 수행한다.

## 소유권에 맞춰 만들 대상

| 영역 | 주요 대상 | 실행 소유자 |
|---|---|---|
| 인증·평가 | tenants, users, tenant_memberships, projects, project_candidates, invitations, consents | Gateway |
| 버전 설정 | competencies/versions, scenario_versions, scenario_competencies, observation_rules, scenario_events | 발행·등록 서비스/Gateway |
| 업무·원본 | sessions, session_event_runs, messages, session_events, tasks, assignment_proposals, document_sections/revisions, assistant_runs/usage_links | Gateway |
| 문서·검색 | company_documents, document_versions, 승인 레코드, document_chunks·벡터·준비/인덱스 상태 | 원문·승인은 Gateway, 검색·인덱스는 rag-mcp |
| 생성·분석 | outbox, report_jobs, reports, observations, evidence_refs | Outbox는 Gateway, 작업·분석·리포트는 워커 |
| 실행 | agent_checkpoints, tool_audit_logs | Agent·각 서비스의 제한된 실행 권한 |

테이블은 설계 대상이며 현재 존재한다고 가정하지 않는다. 규칙 실행기의 관찰 저장은 권한 있는 내부 API를 경유할 수도 있으므로 해당 경로도 DB 권한과 일치시킨다.

## 제약·인덱스·버전 책임

- 기업 소유 데이터의 tenant_id·프로젝트·세션 관계를 일치시키고 교차 기업 참조를 막는다. 공통 catalog의 가시성은 별도로 정의한다.
- 프로젝트의 시나리오·자료·역량·규칙 버전 참조를 저장하고 진행 중 버전을 덮어쓰지 않는다.
- 초대 토큰 원문 대신 해시·만료·소비 상태를 저장한다. 사용자 비밀번호 원문을 저장하지 않는다.
- 사건 실행, 요청/command 멱등 키, 세션 순서, 리포트 스냅샷/버전, 문서 revision에 유일성·조회 인덱스를 둔다.
- 종료와 Outbox를 한 트랜잭션으로 쓸 수 있게 한다. 워커 선점·재시도·완료 상태의 저장 구조를 제공한다.
- 자료 공개·승인 해시·내용 버전·청크/임베딩 모델 버전을 추적한다. 임베딩 차원은 모델 결정과 함께 맞춘다.
- Gateway·Agent·rag-mcp·워커에 데이터 소유권에 맞는 DB 역할을 부여한다. business-mcp에 DB 자격을 주지 않는다.

## 기본 데이터와 경계

역량 값은 competencies를, 시나리오 이름은 scenarios를 참조한다. seed가 별도 목록·사건·관찰 기준을 만들어내지 않는다. fixture는 테스트 DB에만 넣고 실제 기업·지원자 데이터나 발행 시나리오로 배포하지 않는다.

## 완료 기준

빈 DB에 전체 변경을 적용할 수 있고, 기존 DB의 다음 버전 적용 절차가 재현된다. 서비스별 저장 코드와 스키마가 맞고 유일성·기업 격리·인덱스·버전 참조를 통합 테스트로 검증한다. 데이터 손실 가능 변경과 되돌리기 제한을 명시하며 확인 없이 기존 업무 데이터를 초기화하지 않는다.
