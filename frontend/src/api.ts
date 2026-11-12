import type {
  Baseline,
  CompareEntry,
  Job,
  KeyDetail,
  KeysResponse,
  Metadata,
  ResultSample,
  Trial,
} from './types'

const BASE = '/api'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, init)
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      if (typeof body?.detail === 'string') message = body.detail
    } catch {
      /* keep status text */
    }
    throw new Error(message)
  }
  return (await response.json()) as T
}

export const api = {
  health: () => request<{ status: string; dataRoot: string }>('/health'),

  datasets: () =>
    request<{
      datasets: { id: string; name: string; leftRows: number; rightRows: number; hotCount: number }[]
      limits: { maxPartitions: number; minSalt: number; maxSalt: number; maxHotKeys: number }
    }>('/datasets'),

  dataset: (id: string) => request<Metadata>(`/datasets/${encodeURIComponent(id)}`),

  createSample: () => request<Metadata>('/datasets/sample', { method: 'POST' }),

  upload: (left: File, right: File) => {
    const form = new FormData()
    form.append('left', left)
    form.append('right', right)
    return request<Metadata>('/datasets/upload', { method: 'POST', body: form })
  },

  keys: (datasetId: string, q = '', limit = 200, sort = 'hot') =>
    request<KeysResponse>(
      `/datasets/${encodeURIComponent(datasetId)}/keys?q=${encodeURIComponent(q)}&limit=${limit}&sort=${sort}`,
    ),

  plan: (body: { datasetId: string; partitions: number; salt: number; hotKeys: string[] }) =>
    request<Trial>('/plan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),

  compare: (body: { datasetId: string; partitions: number; hotKeys: string[]; salts: number[] }) =>
    request<{ comparisons: CompareEntry[] }>('/plan/compare', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),

  execute: (body: { datasetId: string; partitions: number; salt: number; hotKeys: string[] }) =>
    request<Job>('/execute', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),

  job: (id: string) => request<Job>(`/jobs/${encodeURIComponent(id)}`),

  keyDetail: (jobId: string, key: string) =>
    request<KeyDetail>(`/jobs/${encodeURIComponent(jobId)}/keys/${encodeURIComponent(key)}`),

  resultSample: (jobId: string, limit = 20) =>
    request<ResultSample>(`/jobs/${encodeURIComponent(jobId)}/result?limit=${limit}`),

  baseline: (jobId: string, limit = 20) =>
    request<Baseline>(`/jobs/${encodeURIComponent(jobId)}/baseline?limit=${limit}`),
}

export function formatInt(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return value.toLocaleString('en-US')
}

export function formatCompact(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  if (Math.abs(value) < 10_000) return value.toLocaleString('en-US')
  if (Math.abs(value) < 1_000_000) return `${(value / 1000).toFixed(1)}K`
  return `${(value / 1_000_000).toFixed(2)}M`
}

export function formatBytes(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / (1024 * 1024)).toFixed(2)} MB`
}

export function formatPercent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${(value * 100).toFixed(digits)}%`
}
