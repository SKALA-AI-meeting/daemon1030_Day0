<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { auth, logout } from './stores/auth'
const router = useRouter(), route = useRoute()
const management = computed(() => auth.user?.role === 'company' && (route.path.startsWith('/projects') || route.path === '/company-documents'))
async function exit() { await logout(); router.push('/login') }
</script>
<template>
  <header class="topbar" :class="{ 'office-topbar': $route.path.startsWith('/sessions/') }">
    <RouterLink to="/" class="brand"><span class="brand-mark">D<span>0</span></span><strong>Day Zero</strong></RouterLink>
    <nav v-if="auth.user"><RouterLink :to="auth.user.role === 'company' ? '/projects' : '/assessments'">{{ auth.user.role === 'company' ? '평가 프로젝트' : '나의 평가' }}</RouterLink><RouterLink v-if="auth.user.role === 'company'" to="/company-documents">사내 문서</RouterLink><button class="text-button" @click="exit">로그아웃</button></nav>
  </header>
  <div :class="{ 'management-shell': management }"><aside v-if="management" class="management-sidebar" aria-label="기업 메뉴"><span class="sidebar-caption">기업 워크스페이스</span><RouterLink to="/projects" :class="{ selected: $route.path.startsWith('/projects') }">▦　평가 프로젝트</RouterLink><RouterLink to="/company-documents" :class="{ selected: $route.path === '/company-documents' }">▤　사내 문서</RouterLink><p>평가를 준비하고,<br>함께 일하는 모습을 확인하세요.</p></aside><RouterView :key="$route.path" /></div>
</template>
