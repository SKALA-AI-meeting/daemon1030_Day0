<script setup>
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../../api/client'
import { auth } from '../../stores/auth'
const route = useRoute(), router = useRouter()
const register = ref(false), busy = ref(false), error = ref('')
const form = reactive({ email: '', password: '', name: '', role: 'company', company_name: '' })
async function submit() {
  busy.value = true; error.value = ''
  try {
    auth.user = await api(register.value ? '/auth/register' : '/auth/login', { method: 'POST', body: register.value ? form : { email: form.email, password: form.password } })
    auth.loaded = true
    const next = route.query.next
    router.push(typeof next === 'string' && next.startsWith('/') && !next.startsWith('//') ? next : '/')
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}
</script>
<template>
  <main class="auth-layout">
    <section class="auth-story"><span class="eyebrow">BEFORE DAY ONE</span><h1>이력서 너머,<br>함께 일하는 모습.</h1><p>AI 동료와 협업하는 과정을 통해<br>업무 속 행동과 가능성을 발견합니다.</p><div class="story-line"><span>01</span> 함께 일하고 <span>02</span> 과정을 남기고 <span>03</span> 근거로 이해합니다</div></section>
    <section class="card auth-card"><span class="eyebrow">WELCOME TO DAY ZERO</span><h2>{{ register ? '계정 만들기' : '다시 만나 반가워요' }}</h2><p class="muted">{{ register ? '기업 또는 개인 계정으로 시작하세요.' : '로그인하고 나의 워크스페이스로 이동하세요.' }}</p>
      <form @submit.prevent="submit">
        <div v-if="register" class="segmented"><button type="button" :class="{ selected: form.role === 'company' }" @click="form.role = 'company'">기업 관리자</button><button type="button" :class="{ selected: form.role === 'individual' }" @click="form.role = 'individual'">개인 지원자</button></div>
        <label v-if="register">이름<input v-model="form.name" required maxlength="80" autocomplete="name"></label>
        <label v-if="register && form.role === 'company'">회사 이름<input v-model="form.company_name" required maxlength="100" autocomplete="organization"></label>
        <label>이메일<input v-model="form.email" type="email" required autocomplete="email" placeholder="name@company.com"></label>
        <label>비밀번호<input v-model="form.password" type="password" required :minlength="register ? 10 : 1" maxlength="128" :autocomplete="register ? 'new-password' : 'current-password'" :placeholder="register ? '10자 이상 입력' : '비밀번호'"></label>
        <p v-if="error" role="alert" class="error">{{ error }}</p><button class="primary full" :disabled="busy">{{ busy ? '처리 중…' : register ? '계정 만들기' : '로그인' }}</button>
      </form>
      <button class="text-button auth-toggle" @click="register = !register; error = ''">{{ register ? '이미 계정이 있나요? 로그인' : '처음 방문했나요? 계정 만들기' }}</button>
    </section>
  </main>
</template>
