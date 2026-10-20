<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../../api/client'
const items = ref([]), title = ref(''), content = ref(''), error = ref(''), busy = ref(false)
const labels = { pending: '준비 대기', prepared: '승인 대기', approved: '인덱싱 대기', rejected: '거절됨', indexed: '인덱싱 완료' }
async function load() { try { items.value = await api('/document-imports') } catch (e) { error.value = e.message } }
async function create() {
  busy.value = true; error.value = ''
  try { await api('/document-imports', { method: 'POST', body: { title: title.value, content: content.value } }); title.value = ''; content.value = ''; await load() }
  catch (e) { error.value = e.message } finally { busy.value = false }
}
async function decide(item, approved) {
  try { await api(`/document-imports/${item.id}/decision`, { method: 'POST', body: { approved, content_hash: item.content_hash } }); await load() }
  catch (e) { error.value = e.message }
}
onMounted(load)
</script>
<template><main class="page"><RouterLink to="/projects" class="back">← 프로젝트 목록</RouterLink><div class="page-heading"><div><span class="eyebrow">COMPANY KNOWLEDGE</span><h1>사내 문서 승인</h1><p class="muted">내용 확인과 승인 후 AI가 참고할 수 있도록 준비합니다. 진행 중인 평가의 자료는 바뀌지 않습니다.</p></div><button class="secondary" @click="load">새로고침</button></div><p v-if="error" class="error" role="alert">{{ error }}</p><form class="card" @submit.prevent="create"><h2>문서 등록 준비</h2><label>문서 제목<input v-model="title" required maxlength="150"></label><label>문서 내용<textarea v-model="content" required maxlength="80000" rows="7"></textarea></label><button class="primary" :disabled="busy">{{ busy ? '준비 중…' : '등록 준비' }}</button></form><section v-for="item in items" :key="item.id" class="card"><div class="section-heading"><h2>{{ item.title }}</h2><span class="badge">{{ labels[item.status] }}</span></div><p class="preserve">{{ item.content }}</p><small class="muted">승인 대상 해시: {{ item.content_hash.slice(0, 16) }}…</small><div v-if="item.status === 'prepared'" class="form-footer"><span>위 내용을 사내 검색 자료로 승인하시겠습니까?</span><div><button class="secondary" @click="decide(item, false)">거절</button> <button class="primary" @click="decide(item, true)">확인한 내용 승인</button></div></div></section></main></template>
