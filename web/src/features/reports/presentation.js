export const stateLabels = { invited: '초대됨', ready: '시작 대기', running: '응시 중', paused: '일시정지', submitted: '응시 완료', expired: '초대 만료' }
export const reportLabels = { ready: '리포트 준비', pending: '분석 대기', processing: '분석 중', not_configured: '분석 미설정', failed: '분석 실패' }
export const reviewLabels = { met: '기준 충족', partial: '일부 충족', not_met: '기준 미충족', insufficient_evidence: '근거 부족', not_applicable: '적용 상황 미제공', not_submitted: '작성 내용 없음', unavailable: 'AI 검토 미완료', pending: '검토 대기' }
export function tone(state) { return ['ready', 'submitted', 'met'].includes(state) ? 'green' : ['paused', 'partial', 'not_met', 'not_submitted', 'failed', 'expired'].includes(state) ? 'amber' : '' }
export function duration(seconds) { return `${Math.ceil(seconds / 60)}분` }
export function date(value) { return new Date(value * 1000).toLocaleDateString('ko-KR') }
