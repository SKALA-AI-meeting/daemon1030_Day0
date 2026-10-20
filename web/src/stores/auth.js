import { reactive } from 'vue'
import { api } from '../api/client'

export const auth = reactive({ user: null, loaded: false })
export async function restoreAuth() {
  try { auth.user = await api('/auth/me') }
  catch { auth.user = null }
  auth.loaded = true
}
export async function logout() {
  await api('/auth/logout', { method: 'POST' })
  auth.user = null
}
