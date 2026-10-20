<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { onBeforeRouteLeave, useRoute } from 'vue-router'
import { api, commandId } from '../../api/client'
const route = useRoute(), session = ref(null), events = ref([]), consent = ref(false), error = ref(''), text = ref(''), assistantText = ref(''), channel = ref('team'), tab = ref('document'), busy = ref(false), connected = ref(false), remaining = ref(0), selectedDocument = ref(null), saveStatus = ref('변경 사항이 저장됩니다')
const drafts = reactive({}), dirty = new Set(), revisions = {}, timers = {}, saves = {}, sourceIds = {}
const confirmSubmit = ref(false), recipient = ref('colleague')
const sidebarOpen = ref(!window.matchMedia('(max-width: 640px)').matches), conversation = ref('team')
const conversationTabs = [
  { id: 'team', title: '팀 대화', short: '팀 대화', path: 'M8 3 6 21M18 3l-2 18M3 8h18M2 16h18' },
  { id: 'dm', title: '개인 DM', short: '개인 DM', path: 'M21 11a8 8 0 0 1-8 8H8l-5 3V11a9 9 0 0 1 18 0ZM8 10h8M8 14h5' },
  { id: 'assistant', title: 'AI 어시스턴트', short: '어시스턴트', path: 'm12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z' },
]
const activeConversation = computed(() => conversationTabs.find(item => item.id === conversation.value))
function toggleConversation(id) {
  if (conversation.value === id) sidebarOpen.value = !sidebarOpen.value
  else { conversation.value = id; sidebarOpen.value = true }
  if (id !== 'assistant') channel.value = id
}
function formatElapsed(seconds) { return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(Math.floor(seconds % 60)).padStart(2, '0')}` }
let socket, pingTimer, clockTimer, reconnectTimer, disposed = false
const chat = computed(() => events.value.filter(e => (e.type === 'message.created' && e.payload.channel === channel.value) || (e.type === 'scenario.event' && channel.value === 'team')))
const assistant = computed(() => events.value.filter(e => e.type === 'message.created' && e.payload.channel === 'assistant'))
const unavailable = computed(() => events.value.filter(e => e.type === 'agent.unavailable').at(-1))
const proposal = computed(() => {
  const latest = events.value.filter(e => e.type === 'assignment.proposed').at(-1)
  if (!latest || events.value.some(e => ['assignment.applied','assignment.decision'].includes(e.type) && e.payload.proposal_id === latest.id)) return null
  return latest
})
const clock = computed(() => `${String(Math.floor(remaining.value / 60)).padStart(2, '0')}:${String(Math.floor(remaining.value % 60)).padStart(2, '0')}`)
function actorName(actor) { return session.value?.role_names?.[actor] || ({ candidate: '나', colleague: 'AI 동료', leader: '팀장', assistant: 'AI 어시스턴트', system: '시스템' })[actor] || actor }
async function load() {
  const result = await api(`/sessions/${route.params.id}`)
  session.value = result; remaining.value = result.remaining_seconds
  for (const section of result.workspace.sections) {
    if (!dirty.has(section.id) && !saves[section.id]) { drafts[section.id] = section.content; revisions[section.id] = section.revision }
  }
}
async function loadEvents() {
  let cursor = 0
  const all = []
  while (true) {
    const batch = await api(`/sessions/${route.params.id}/events?after=${cursor}`)
    all.push(...batch)
    if (batch.length < 500) break
    cursor = batch.at(-1).seq
  }
  events.value = all
}
async function start() {
  busy.value = true; error.value = ''
  try {
    if (!session.value.consented) await api(`/sessions/${route.params.id}/consent`, { method: 'POST', body: { accepted: true, version: 'v1' } })
    await api(`/sessions/${route.params.id}/start`, { method: 'POST' }); await load(); connect()
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
function connect() {
  if (disposed || !['running', 'paused'].includes(session.value?.state)) return
  const after = events.value.at(-1)?.seq || 0
  socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/sessions/${route.params.id}/ws?after=${after}`)
  socket.onopen = () => { connected.value = true; session.value.state = 'running'; socket.send('ping') }
  socket.onmessage = async ({ data }) => {
    const item = JSON.parse(data)
    if (item.type === 'clock') { remaining.value = item.remaining_seconds; return }
    if (item.seq && !events.value.some(e => e.id === item.id)) events.value.push(item)
    if (['document.updated', 'task.updated', 'assignment.applied', 'session.closed', 'scenario.event'].includes(item.type)) {
      try { await load() } catch (e) { error.value = e.message }
    }
    if (item.type === 'session.closed') { await loadEvents(); socket.close() }
  }
  socket.onclose = (event) => {
    connected.value = false
    if (event.code === 4409) { error.value = '다른 창에서 응시를 이어가고 있습니다. 이 창은 연결을 종료했습니다.'; return }
    if (!disposed && session.value?.state !== 'submitted') {
      session.value.state = 'paused'
      reconnectTimer = setTimeout(connect, 3000)
    }
  }
}
function changed(id) {
  dirty.add(id); saveStatus.value = '저장 대기 중'
  clearTimeout(timers[id]); timers[id] = setTimeout(() => saveSection(id).catch(() => {}), 700)
}
async function saveSection(id) {
  if (saves[id]) return saves[id]
  saves[id] = (async () => {
    while (dirty.has(id)) {
      const content = drafts[id]
      saveStatus.value = '저장 중…'
      try {
        const item = await api(`/sessions/${route.params.id}/documents/${id}`, { method: 'PUT', body: { content, expected_revision: revisions[id], command_id: commandId(), source_response_id: sourceIds[id] || null } })
        revisions[id] = item.revision
        if (drafts[id] === content) { dirty.delete(id); delete sourceIds[id] }
        saveStatus.value = dirty.size ? '저장 대기 중' : '모든 변경 사항 저장됨'
      } catch (e) { error.value = e.message; saveStatus.value = '저장 실패 · 입력 내용은 화면에 유지됩니다'; throw e }
    }
  })()
  try { await saves[id] } finally { delete saves[id] }
}
async function saveAll() { await Promise.all([...dirty].map(saveSection)) }
async function send(isAssistant = false) {
  const input = isAssistant ? assistantText : text
  if (!input.value.trim()) return
  const content = input.value
  try {
    const item = await api(`/sessions/${route.params.id}/messages`, { method: 'POST', body: { content, channel: isAssistant ? 'assistant' : channel.value, recipient: recipient.value, command_id: commandId() } })
    if (!events.value.some(e => e.id === item.id)) events.value.push(item)
    if (input.value === content) input.value = ''
  } catch (e) { error.value = e.message }
}
async function updateTask(task, value) {
  try { await api(`/sessions/${route.params.id}/tasks/${task.id}`, { method: 'PUT', body: { status: value, expected_revision: task.revision, command_id: commandId() } }); await load() } catch (e) { error.value = e.message }
}
async function decideAssignment(approved) {
  try {
    await api(`/sessions/${route.params.id}/assignments/${proposal.value.id}/decision`, { method: 'POST', body: { approved } })
    await load(); await loadEvents()
    if (!approved) { text.value = '분담안을 다시 조정하고 싶습니다. '; channel.value = 'team' }
  } catch (e) { error.value = e.message }
}
async function submit() {
  confirmSubmit.value = false
  busy.value = true; error.value = ''
  try { await saveAll(); await api(`/sessions/${route.params.id}/submit`, { method: 'POST' }); await load(); await loadEvents(); socket?.close() }
  catch (e) { error.value = e.message } finally { busy.value = false }
}
function useResponse(event) {
  const section = session.value.workspace.sections.find(s => s.assignee === 'candidate')
  if (!section) return
  drafts[section.id] = event.payload.content; sourceIds[section.id] = event.id; changed(section.id)
}
function beforeUnload(event) { if (dirty.size) { event.preventDefault(); event.returnValue = '' } }
onBeforeRouteLeave(async () => { try { if (dirty.size) await saveAll(); return true } catch { return confirm('저장하지 못한 내용이 있습니다. 화면을 나가시겠어요?') } })
onMounted(async () => {
  try { await load(); await loadEvents(); if (session.value.state === 'running' || session.value.state === 'paused') connect() }
  catch (e) { error.value = e.message }
  pingTimer = setInterval(() => { if (socket?.readyState === WebSocket.OPEN) socket.send('ping') }, 5000)
  clockTimer = setInterval(() => { if (connected.value && session.value?.state === 'running') remaining.value = Math.max(0, remaining.value - 1) }, 1000)
  window.addEventListener('beforeunload', beforeUnload)
})
onBeforeUnmount(() => { disposed = true; clearInterval(pingTimer); clearInterval(clockTimer); clearTimeout(reconnectTimer); Object.values(timers).forEach(clearTimeout); socket?.close(); window.removeEventListener('beforeunload', beforeUnload) })
</script>
<template>
  <main v-if="!session" class="page"><p v-if="error" class="error">{{ error }}</p><p v-else>평가를 불러오고 있습니다…</p></main>
  <main v-else-if="session.state === 'ready'" class="page narrow prep-page"><RouterLink to="/assessments" class="back">나의 평가 / 응시 준비</RouterLink><div class="page-heading"><div><h1>시작하기 전에 확인해 주세요.</h1><p class="muted">{{ session.title }} · {{ session.scenario_title }}</p></div></div><section class="card"><span class="eyebrow">BEFORE YOU START</span><h2>AI 동료와 함께 과제를 수행합니다.</h2><p class="muted">제한 시간 {{ Math.ceil(session.duration_seconds / 60) }}분 · 팀의 대화와 자료를 확인하고 결과물을 정리해 주세요.</p><details class="prep-brief" open><summary>과제 안내</summary><p class="preserve">{{ session.brief || '과제 안내는 시나리오 설정에 따라 표시됩니다.' }}</p></details><div v-if="session.is_fixture" class="notice">개발용 기능 확인입니다. 실제 채용 평가와 역량 분석을 제공하지 않습니다.</div><article class="prep-item"><span class="person-avatar">01</span><div><h3>가상 오피스 사용법</h3><p>왼쪽에서 팀 대화·개인 DM·어시스턴트를 전환합니다. 대화 창을 접으면 오른쪽 작성 공간을 넓게 쓸 수 있습니다.</p></div></article><article class="prep-item"><span class="person-avatar">02</span><div><h3>제한 시간과 연결 종료</h3><p>서버가 연결 종료를 감지하면 시간을 일시정지합니다. 재접속하면 저장된 내용과 남은 시간으로 이어서 응시합니다.</p></div></article><article class="prep-item"><span class="person-avatar">03</span><div><h3>저장과 최종 제출</h3><p>작성 내용은 자동 저장됩니다. 제한 시간 전에도 언제든 최종 제출할 수 있으며, 제출 후에는 수정할 수 없습니다.</p></div></article><div class="notice"><strong>기록 수집과 관찰 범위</strong><p>대화·문서 수정·태스크·AI 요청과 결과 활용이 기록됩니다. 리포트는 기업에 제공되며 최종 채용 판단은 기업 담당자가 합니다.</p><p>관찰 역량: {{ session.competencies.map(c => c.name).join(', ') }}</p></div><label class="consent-option"><input v-model="consent" type="checkbox"><span><strong>안내와 기록 방식에 동의합니다.</strong><small>동의한 뒤 응시 시작 버튼을 눌러 주세요.</small></span></label><p v-if="error" class="error" role="alert">{{ error }}</p><div class="form-footer"><span class="muted">버튼을 누르면 제한 시간이 시작됩니다.</span><button class="primary" :disabled="!consent || busy" @click="start">{{ busy ? '시작 중…' : '응시 시작 →' }}</button></div></section></main>
  <main v-else-if="session.state === 'submitted'" class="page narrow"><section class="card complete-card"><span class="complete-icon">✓</span><span class="eyebrow">DAY ZERO COMPLETE</span><h1>응시가 완료되었습니다</h1><p class="muted">작성한 내용과 활동 기록이 저장되었습니다.</p><div class="notice">{{ session.is_fixture ? '관찰 기준이 없는 개발 실행입니다. 역량 분석 결과는 생성하지 않습니다.' : '업무 중 설정에 따라 연출된 상황이 포함될 수 있습니다. 리포트는 기업에 제공되며 최종 판단은 사람이 합니다.' }}</div><div v-for="event in events.filter(e => e.type === 'scenario.event')" :key="event.id" class="report-item"><p>{{ event.payload.content }}</p></div><RouterLink class="primary button-link" to="/assessments">나의 평가로 돌아가기</RouterLink></section></main>
  <main v-else class="office office-shell">
    <header class="office-heading office-toolbar">
      <div class="office-project"><span class="eyebrow">MY WORKSPACE</span><h2>{{ session.title }}</h2></div>
      <div class="office-controls">
        <span class="office-connection" :class="{ online: connected }"><i></i>{{ connected ? '연결됨' : '재연결 중 · 시간 일시정지' }}</span>
        <div class="office-timer" :class="{ urgent: remaining < 300 }"><small>{{ connected ? '남은 시간' : '시간 일시정지' }}</small><span class="clock">{{ clock }}</span></div>
        <button class="primary" :disabled="busy || !connected" @click="confirmSubmit = true">{{ busy ? '제출 중…' : '최종 제출' }}</button>
      </div>
    </header>
    <section v-if="confirmSubmit" role="dialog" aria-label="최종 제출 확인" class="office-confirm"><div><strong>최종 제출하시겠어요?</strong><p>저장 중인 내용을 먼저 저장합니다. 제출 후에는 수정할 수 없습니다.</p></div><div><button class="secondary" @click="confirmSubmit = false">계속 작성</button><button class="primary" :disabled="busy" @click="submit">확인하고 제출</button></div></section>
    <div v-if="session.is_fixture" class="fixture-banner">기능 확인용 워크스페이스 · 실제 평가 아님 · 분석 미설정</div>
    <p v-if="error" class="error" role="alert">{{ error }} <button class="text-button" @click="error = ''; saveAll().catch(() => {})">저장 재시도</button></p>
    <div v-if="proposal" class="office-assignment"><details><summary>AI 동료가 업무 분담안을 제안했습니다</summary><p v-for="[id, owner] in Object.entries(proposal.payload.assignments)" :key="id">{{ session.workspace.tasks.find(t => t.id === id)?.title || id }}: {{ owner === 'candidate' ? '내 담당' : '동료 담당' }}</p></details><div><button class="secondary" @click="decideAssignment(false)">조정 요청</button><button class="primary" @click="decideAssignment(true)">수락</button></div></div>
    <div class="office-layout" :class="{ 'sidebar-open': sidebarOpen }">
      <nav class="office-rail" aria-label="대화 사이드바">
        <div class="office-rail-label">소통</div>
        <button v-for="item in conversationTabs" :key="item.id" type="button" :class="{ active: sidebarOpen && conversation === item.id }" :aria-label="item.title + (sidebarOpen && conversation === item.id ? ' 접기' : ' 열기')" :aria-pressed="sidebarOpen && conversation === item.id" aria-controls="office-conversation" :title="item.title" @click="toggleConversation(item.id)"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="item.path" /></svg><span>{{ item.short }}</span></button>
        <span class="office-rail-bottom" title="가상 오피스">D<span>0</span></span>
      </nav>
      <button v-if="sidebarOpen" class="office-sidebar-scrim" aria-label="대화 창 닫기" @click="sidebarOpen = false"></button>
      <aside v-if="sidebarOpen" id="office-conversation" class="office-panel office-conversation" :aria-label="activeConversation.title">
        <div class="office-panel-heading"><div><span class="office-kicker">{{ conversation === 'assistant' ? 'PERSONAL ASSISTANT' : 'TEAM COMMUNICATION' }}</span><h3>{{ activeConversation.title }}</h3></div><button class="office-icon-button" aria-label="대화 패널 접기" title="대화 패널 접기" @click="sidebarOpen = false"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="m14 6-6 6 6 6M20 4v16" /></svg></button></div>
        <template v-if="conversation !== 'assistant'">
          <div class="office-channel-info"><span class="office-channel-symbol">{{ channel === 'team' ? '#' : '@' }}</span><div><strong>{{ channel === 'team' ? '함께 일하는 공간' : '직원에게 직접 이야기하기' }}</strong><small>{{ channel === 'team' ? '팀 공지와 업무 대화를 확인하세요.' : '아래에서 대화할 AI 직원을 선택하세요.' }}</small></div></div>
          <div class="messages office-message-list" aria-live="polite" aria-relevant="additions">
            <div v-if="!chat.length" class="office-chat-empty"><span>{{ channel === 'team' ? '#' : '@' }}</span><h4>{{ channel === 'team' ? '팀 대화를 시작해 보세요' : '필요한 이야기를 직접 전하세요' }}</h4><p>질문하거나 진행 상황을 공유하며<br>AI 직원과 업무를 이어가세요.</p></div>
            <article v-for="e in chat" :key="e.id" class="message office-message" :class="{ own: e.payload.actor === 'candidate', announcement: e.type === 'scenario.event' }"><small>{{ e.payload.actor_name || actorName(e.payload.actor) }}<time>{{ formatElapsed(e.elapsed) }}</time></small><p>{{ e.payload.content }}</p></article>
          </div>
          <div class="office-composer"><label class="office-recipient">대화 상대<select v-model="recipient" aria-label="응답할 AI 직원"><option value="colleague">{{ actorName('colleague') }}</option><option value="leader">{{ actorName('leader') }}</option></select></label><form @submit.prevent="send(false)"><textarea v-model="text" aria-label="팀 메시지" :placeholder="channel === 'team' ? '팀에 메시지 남기기…' : '선택한 직원에게 메시지 보내기…'" maxlength="12000" :disabled="!connected"></textarea><div class="office-composer-footer"><small>{{ channel === 'team' ? '팀 채널' : '개인 DM' }}</small><button class="primary" :disabled="!text.trim() || !connected">전송 <span aria-hidden="true">↑</span></button></div></form></div>
        </template>
        <template v-else>
          <p class="office-assistant-note">생각을 정리하고, 자료를 살펴보고, 초안을 만들어 보세요. 활용 여부는 직접 결정할 수 있어요.</p>
          <div class="messages office-message-list" aria-live="polite" aria-relevant="additions"><div v-if="!assistant.length" class="office-chat-empty assistant-empty"><span>✦</span><h4>어떤 작업을 도와드릴까요?</h4><p>업무 중 필요한 도움을<br>이곳에서 요청하세요.</p><button type="button" @click="assistantText = '현재 공개된 자료의 핵심 내용을 정리해 줘.'">공개된 자료 정리하기 ↗</button><button type="button" @click="assistantText = '작성 중인 내용에서 추가로 확인할 점을 함께 정리해 줘.'">확인할 내용 정리하기 ↗</button></div><article v-for="e in assistant" :key="e.id" class="message office-message" :class="{ own: e.payload.actor === 'candidate' }"><small>{{ actorName(e.payload.actor) }}<time>{{ formatElapsed(e.elapsed) }}</time></small><p>{{ e.payload.content }}</p><small v-if="e.payload.citations?.length">참고: {{ e.payload.citations.map(c => c.title).join(', ') }}</small><button v-if="e.payload.actor === 'assistant' && connected" class="text-button" @click="useResponse(e)">첫 내 담당 문서에 사용 ↗</button></article><p v-if="unavailable" class="notice">{{ unavailable.payload.message }}</p></div>
          <div class="office-composer"><form @submit.prevent="send(true)"><textarea v-model="assistantText" aria-label="어시스턴트 요청" placeholder="어시스턴트에게 요청하기…" maxlength="12000" :disabled="!connected"></textarea><div class="office-composer-footer"><small>AI 어시스턴트</small><button class="primary" :disabled="!assistantText.trim() || !connected">요청 <span aria-hidden="true">↑</span></button></div></form></div>
        </template>
      </aside>
      <section class="office-panel office-editor" aria-label="작성 워크스페이스">
        <div class="office-panel-heading"><div><span class="office-kicker">WORKSPACE</span><h3>나의 작업 공간</h3></div><span class="office-save-status" :class="{ warning: saveStatus.includes('실패') }" role="status"><span aria-hidden="true">{{ saveStatus.includes('실패') ? '!' : saveStatus === '모든 변경 사항 저장됨' ? '✓' : '·' }}</span> {{ saveStatus }}</span></div>
        <div class="office-editor-tabs" aria-label="작업 영역 선택"><button v-for="[key, label] in [['document','과제 문서'],['tasks','태스크'],['resources','사내 문서']]" :key="key" :class="{ active: tab === key }" :aria-pressed="tab === key" @click="tab = key">{{ label }}<span v-if="key === 'resources'">{{ session.documents.length }}</span></button></div>
        <div class="work-content office-editor-content">
          <details class="office-brief"><summary>업무 안내 <span>과제 목표 다시 보기</span></summary><p>{{ session.brief || '과제 안내가 아직 설정되지 않았습니다.' }}</p></details>
          <template v-if="tab === 'document'"><div class="office-document-intro"><span class="office-kicker">YOUR DOCUMENTS</span><h3>생각을 정리하고, 결과를 남겨 주세요.</h3><p>담당 문서는 작성하는 동안 자동으로 저장됩니다.</p></div><p v-if="!session.workspace.sections.length" class="empty-note">문서 양식이 아직 설정되지 않았습니다.</p><article v-for="(s, index) in session.workspace.sections" :key="s.id" class="document-section office-document-section" :class="{ 'colleague-section': s.assignee !== 'candidate' }"><div class="office-document-title"><span class="office-section-number">{{ String(index + 1).padStart(2, '0') }}</span><label :for="'section-' + s.id">{{ s.title }}</label><span class="badge">{{ s.assignee === 'candidate' ? '내 담당' : '동료 담당 · 읽기 전용' }}</span></div><textarea :id="'section-' + s.id" v-model="drafts[s.id]" :readonly="s.assignee !== 'candidate' || !connected" :placeholder="s.assignee === 'candidate' ? '이곳에 내용을 작성하세요…' : '동료가 공유한 초안이 이곳에 표시됩니다.'" @input="changed(s.id)"></textarea></article></template>
          <template v-else-if="tab === 'tasks'"><div class="office-document-intro"><span class="office-kicker">TASK BOARD</span><h3>할 일과 진행 상황</h3><p>내 담당 업무의 상태를 변경하고, 동료의 진행 상황을 확인하세요.</p></div><p v-if="!session.workspace.tasks.length" class="empty-note">설정된 태스크가 없습니다.</p><article v-for="task in session.workspace.tasks" :key="task.id" class="task-row"><div><strong>{{ task.title }}</strong><small>{{ task.assignee === 'candidate' ? '내 담당' : 'AI 동료 담당' }}</small></div><select :value="task.status" :aria-label="task.title + ' 상태'" :disabled="task.assignee !== 'candidate' || !connected" @change="updateTask(task, $event.target.value)"><option value="todo">할 일</option><option value="doing">진행 중</option><option value="done">완료</option></select></article></template>
          <template v-else><div class="office-document-intro"><span class="office-kicker">COMPANY LIBRARY</span><h3>업무에 필요한 자료</h3><p>현재 공개된 문서에서 필요한 정보를 확인하세요.</p></div><p v-if="!session.documents.length" class="empty-note">현재 공개된 사내 문서가 없습니다.</p><button v-for="d in session.documents" :key="d.id" class="resource-button" :class="{ 'resource-selected': selectedDocument?.id === d.id }" @click="selectedDocument = d"><span>▤</span> {{ d.title }} <span>↗</span></button><article v-if="selectedDocument" class="office-resource-content"><span class="office-kicker">DOCUMENT</span><h3>{{ selectedDocument.title }}</h3><p class="preserve">{{ selectedDocument.content }}</p></article></template>
        </div>
      </section>
    </div>
  </main>
</template>
<style src="./office.css" scoped></style>
