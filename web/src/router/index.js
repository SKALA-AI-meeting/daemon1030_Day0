import { createRouter, createWebHistory } from 'vue-router'
import { auth, restoreAuth } from '../stores/auth'
import AuthPage from '../features/auth/AuthPage.vue'
import ProjectsPage from '../features/projects/ProjectsPage.vue'
import ProjectPage from '../features/projects/ProjectPage.vue'
import CreateProjectPage from '../features/projects/CreateProjectPage.vue'
import ReportPage from '../features/reports/ReportPage.vue'
import DocumentsPage from '../features/projects/DocumentsPage.vue'
import AssessmentsPage from '../features/assessments/AssessmentsPage.vue'
import InvitePage from '../features/assessments/InvitePage.vue'
import OfficePage from '../features/office/OfficePage.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: AuthPage, meta: { public: true } },
    { path: '/', redirect: () => auth.user?.role === 'company' ? '/projects' : '/assessments' },
    { path: '/projects', component: ProjectsPage, meta: { role: 'company' } },
    { path: '/company-documents', component: DocumentsPage, meta: { role: 'company' } },
    { path: '/projects/new', component: CreateProjectPage, meta: { role: 'company' } },
    { path: '/projects/:id/reports/:sessionId', component: ReportPage, meta: { role: 'company' } },
    { path: '/projects/:id', component: ProjectPage, meta: { role: 'company' } },
    { path: '/assessments', component: AssessmentsPage, meta: { role: 'individual' } },
    { path: '/invite/:token', component: InvitePage, meta: { role: 'individual' } },
    { path: '/sessions/:id', component: OfficePage, meta: { role: 'individual' } },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
router.beforeEach(async to => {
  if (!auth.loaded) await restoreAuth()
  if (!to.meta.public && !auth.user) return { path: '/login', query: { next: to.fullPath } }
  if (to.meta.role && auth.user?.role !== to.meta.role) return '/'
})
