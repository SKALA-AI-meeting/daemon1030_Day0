<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api, commandId } from '../../api/client'
import { duration } from '../reports/presentation'
const router = useRouter(), step = ref(1), title = ref(''), competencies = ref([]), chosen = ref([]), options = ref([]), scenario = ref(''), error = ref(''), busy = ref(false), loading = ref(false), requestId = commandId()
const eligible = computed(() => chosen.value.length >= 3 && chosen.value.length <= 6)
const selected = computed(() => options.value.find(s => s.id === scenario.value))
let version = 0
watch(chosen, async () => {
  const current = ++version
  options.value = []; scenario.value = ''; error.value = ''; loading.value = false
  if (!eligible.value) return
  loading.value = true
  try { const data = await api('/scenario-options', { method: 'POST', body: { competency_ids: chosen.value } }); if (current === version) options.value = data }
  catch (e) { if (current === version) error.value = e.message }
  finally { if (current === version) loading.value = false }
}, { deep: true })
async function create() {
  if (busy.value || !selected.value || !eligible.value || !title.value.trim()) return
  busy.value = true; error.value = ''
  try { const p = await api('/projects', { method: 'POST', body: { title: title.value.trim(), competency_ids: chosen.value, scenario_version_id: scenario.value, request_id: requestId } }); router.push(`/projects/${p.id}`) }
  catch (e) { error.value = e.message } finally { busy.value = false }
}
onMounted(async () => { try { competencies.value = await api('/competencies') } catch (e) { error.value = e.message } })
</script>
<template><main class="page narrow"><RouterLink class="back" to="/projects">평가 프로젝트 / 새 프로젝트</RouterLink><div class="page-heading"><div><h1>{{ ['새 평가 프로젝트를 만드세요.', '어떤 역량을 관찰할까요?', '평가 시나리오를 선택하세요.'][step - 1] }}</h1><p class="muted">관찰할 역량과 시나리오를 정하고 지원자를 초대하세요.</p></div></div><ol class="creation-steps"><li v-for="(label,i) in ['기본 정보','역량 선택','시나리오·확인']" :key="label" :class="{ active: step === i + 1, done: step > i + 1 }"><span>{{ step > i + 1 ? '✓' : i + 1 }}</span>{{ label }}</li></ol><p v-if="error" class="error" role="alert">{{ error }}</p><form class="card" @submit.prevent="step < 3 ? step++ : create()"><template v-if="step === 1"><h2>기본 정보</h2><p class="muted">지원자도 확인할 수 있는 프로젝트 이름을 입력하세요.</p><label class="field-label">프로젝트 이름<input v-model="title" required maxlength="120" placeholder="예: 주니어 프로덕트 매니저 채용"></label></template><template v-else-if="step === 2"><div class="review-heading"><h2>관찰 역량</h2><span class="badge">{{ chosen.length }} / 6 선택</span></div><p class="muted">최소 3개, 최대 6개를 선택하세요.</p><div class="competency-grid"><label v-for="c in competencies" :key="c.id" class="competency-card" :class="{ checked: chosen.includes(c.id) }"><input v-model="chosen" type="checkbox" :value="c.id"><span><strong>{{ c.name }}</strong><small>{{ c.description }}</small></span></label></div><p v-if="loading" class="notice" role="status">가능한 시나리오를 확인하고 있습니다…</p><p v-else-if="!eligible" class="notice">역량을 3개 이상 선택하면 가능한 시나리오를 확인할 수 있습니다.</p><p v-else-if="!options.length && !error" class="notice">선택한 역량을 모두 지원하는 발행 시나리오가 없습니다. 선택 역량을 조정해 주세요.</p><p v-else-if="options.length" class="notice">선택 역량을 모두 지원하는 시나리오 {{ options.length }}개가 있습니다.</p></template><template v-else><h2>{{ title }}</h2><div class="chips"><span v-for="c in competencies.filter(c => chosen.includes(c.id))" :key="c.id" class="badge">{{ c.name }}</span></div><h3 class="section-title">가능한 시나리오</h3><label v-for="s in options" :key="s.id" class="scenario-choice"><input v-model="scenario" required type="radio" :value="s.id"><span><strong>{{ s.title }}</strong><small class="muted">제한 시간 {{ duration(s.duration_seconds) }} · {{ s.id }}</small><span v-if="s.review_available" class="badge">최종 결과물 · AI 평가</span><span v-if="s.is_fixture" class="badge amber">개발용 · 실제 평가 아님</span></span></label><p class="notice">생성 후 역량과 시나리오 버전이 고정됩니다. 같은 프로젝트의 지원자에게 동일한 설정을 제공합니다.</p></template><div class="form-footer"><RouterLink v-if="step === 1" class="muted" to="/projects">취소하고 목록으로</RouterLink><button v-else type="button" class="secondary" :disabled="busy" @click="step--">← 이전</button><button class="primary" :disabled="busy || (step === 1 && !title.trim()) || (step === 2 && (!eligible || !options.length || loading)) || (step === 3 && !selected)">{{ busy ? '생성 중…' : step === 3 ? '프로젝트 생성' : '다음 →' }}</button></div></form></main></template>
