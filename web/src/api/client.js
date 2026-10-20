export async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, {
    credentials: 'same-origin',
    ...options,
    headers: { 'Content-Type': 'application/json', ...options.headers },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map(d => d.msg).join(', ') : data.detail
    throw Object.assign(new Error(detail || '요청을 처리하지 못했습니다.'), { status: response.status })
  }
  return data
}
export const commandId = () => crypto.randomUUID()
