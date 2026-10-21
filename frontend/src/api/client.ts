const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api'

class ApiError extends Error {
  toString() { return this.message }
}

async function responseError(response: Response): Promise<ApiError> {
  const body = await response.text()
  let message = ''
  try {
    const payload = JSON.parse(body)
    if (typeof payload?.detail === 'string') message = payload.detail
    else if (Array.isArray(payload?.detail)) {
      message = payload.detail.map((item: { msg?: unknown }) => typeof item?.msg === 'string' ? item.msg : '').filter(Boolean).join('；')
    } else if (typeof payload?.message === 'string') message = payload.message
  } catch {
    if (response.headers.get('content-type')?.includes('text/plain')) message = body
  }
  return new ApiError(message.slice(0, 2000) || `请求失败（HTTP ${response.status}），请稍后重试。`)
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) }, ...init })
  if (!response.ok) {
    throw await responseError(response)
  }
  return response.json()
}

export async function upload<T>(path: string, file: File): Promise<T> {
  const form = new FormData()
  form.append('file', file)
  const response = await fetch(`${API_BASE}${path}`, { method: 'POST', body: form })
  if (!response.ok) throw await responseError(response)
  return response.json()
}

export { API_BASE }
