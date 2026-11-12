import type { KeyRow, Trial } from './types'

/** Display helpers shared across panels. */

export const SERIES = [
  'var(--series-1)',
  'var(--series-2)',
  'var(--series-3)',
  'var(--series-4)',
  'var(--series-5)',
  'var(--series-6)',
  'var(--series-7)',
  'var(--series-8)',
]

export function seriesColor(index: number): string {
  return SERIES[index % SERIES.length]
}

export function maxBucketOf(trial: Trial | null): number {
  if (!trial) return 0
  if (trial.maxBucket) return trial.maxBucket.rows
  return trial.maxOrdinaryPartition?.rows ?? 0
}

export function skewRatio(trial: Trial | null): number {
  if (!trial || !trial.salted || !trial.maxBucket) return 0
  const ordinary = trial.maxOrdinaryPartition?.rows ?? 0
  if (ordinary <= 0) return 0
  return trial.maxBucket.rows / ordinary
}

export function keyDisplay(key: string): string {
  return key === '' ? '<空字符串>' : key
}

export function keyMatches(row: KeyRow, needle: string): boolean {
  if (!needle) return true
  return row.key.toLowerCase().includes(needle.toLowerCase())
}

export function pct(part: number, whole: number): number {
  if (!whole) return 0
  return part / whole
}

/** Round tick values for a linear axis: 0 / 1,000 / 2,000 style. */
export function niceTicks(max: number, count = 4): number[] {
  if (max <= 0) return [0]
  // Small maxima get whole-number ticks; fractional steps would round to duplicates.
  if (max <= count) {
    return Array.from({ length: Math.floor(max) + 1 }, (_, index) => index)
  }
  const rawStep = max / count
  const magnitude = Math.pow(10, Math.floor(Math.log10(rawStep)))
  const normalised = rawStep / magnitude
  const factor = normalised <= 1 ? 1 : normalised <= 2 ? 2 : normalised <= 5 ? 5 : 10
  const step = factor * magnitude
  const ticks: number[] = []
  for (let value = 0; value <= max + step * 0.001; value += step) ticks.push(Math.round(value))
  if (ticks[ticks.length - 1] < max) ticks.push(ticks[ticks.length - 1] + step)
  return [...new Set(ticks)]
}
