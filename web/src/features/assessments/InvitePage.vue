<script setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../../api/client'
const route = useRoute(), router = useRouter(), error = ref('')
onMounted(async () => { try { const result = await api('/invitations/redeem', { method: 'POST', body: { token: route.params.token } }); router.replace(`/sessions/${result.session_id}`) } catch (e) { error.value = e.message } })
</script>
<template><main class="page narrow"><section class="card"><h1>평가 초대 확인</h1><p v-if="error" class="error" role="alert">{{ error }}</p><p v-else class="muted">본인에게 발급된 초대를 확인하고 있습니다…</p><RouterLink to="/assessments" class="back">나의 평가로 이동 →</RouterLink></section></main></template>
