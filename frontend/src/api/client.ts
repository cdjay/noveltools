const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api'

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) }, ...init })
  if (!response.ok) {
    const body = await response.text()
    throw new Error(body || `请求失败（${response.status}）`)
  }
  return response.json()
}

export async function upload<T>(path: string, file: File): Promise<T> {
  const form = new FormData()
  form.append('file', file)
  const response = await fetch(`${API_BASE}${path}`, { method: 'POST', body: form })
  if (!response.ok) throw new Error(await response.text())
  return response.json()
}

export { API_BASE }

