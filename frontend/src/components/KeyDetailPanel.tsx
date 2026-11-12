import { useMemo, useState } from 'react'
import type { KeyDetail } from '../types'
import { formatInt } from '../api'

/**
 * Drill-down for one hot key: which bucket each side's rows went to, how much
 * was replicated, and whether the partitioned path lost or duplicated rows.
 */

export function KeyDetailPanel({ detail }: { detail: KeyDetail }) {
  const [batchFilter, setBatchFilter] = useState<number | null>(null)
  const totalLeft = detail.left.sourceRows
  const totalRight = detail.right.sourceRows
  const written = detail.left.rowsWritten - totalLeft
  const copied = detail.right.copiesWritten
  const accounts = detail.left.lostRows === 0 && detail.left.duplicateWrites === 0 && detail.join.matchesLeftTimesRight

  const batches = useMemo(() => {
    if (batchFilter === null) return detail.batches
    return detail.batches.filter((batch) => batch.batch === batchFilter)
  }, [detail.batches, batchFilter])

  return (
    <div className="detail">
      <div className="detail-head">
        <div>
          <div className="eyebrow">{detail.empty ? '空键（不参与关联）' : detail.mode === 'salted' ? '热键 · 已拆分' : '普通键'}</div>
          <h3 className="detail-title mono">{detail.keyLabel}</h3>
        </div>
        <div className={`verdict verdict-mini ${accounts ? 'verdict-good' : 'verdict-bad'}`}>
          <span className="verdict-icon" aria-hidden="true">{accounts ? '✓' : '✕'}</span>
          <span>
            {detail.empty
              ? '空键按 SQL 规则不匹配，已单独统计'
              : accounts
                ? `左×右 = ${formatInt(detail.join.expectedRows)}，逐桶合计 ${formatInt(detail.join.rows)}`
                : '行数或重复次数不一致'}
          </span>
        </div>
      </div>

      {detail.note ? <p className="muted small">{detail.note}</p> : null}

      <div className="tile-row detail-tiles">
        <div className="tile">
          <div className="tile-label">左表记录</div>
          <div className="tile-value tabular">{formatInt(totalLeft)}</div>
          <div className="tile-detail">
            写入 {formatInt(detail.left.rowsWritten)} · 丢失 {formatInt(detail.left.lostRows)} · 重复写 {formatInt(detail.left.duplicateWrites)}
          </div>
        </div>
        <div className="tile">
          <div className="tile-label">右表记录</div>
          <div className="tile-value tabular">{formatInt(totalRight)}</div>
          <div className="tile-detail">写入 {formatInt(detail.right.rowsWritten)} · 复制 {formatInt(copied)} 行</div>
        </div>
        <div className="tile">
          <div className="tile-label">产生的关联行数</div>
          <div className="tile-value tabular">{formatInt(detail.join.rows)}</div>
          <div className="tile-detail">
            {detail.empty ? '空键不产生关联行' : `期望 左×右 = ${formatInt(detail.join.expectedRows)}`}
          </div>
        </div>
        <div className="tile">
          <div className="tile-label">每对重复次数</div>
          <div className="tile-value tabular">
            {detail.empty ? '—' : detail.join.duplicatesPerPair === null ? '—' : detail.join.duplicatesPerPair.toFixed(1)}
          </div>
          <div className="tile-detail">
            {detail.empty ? '空键不产生关联对' : '理想值 1.0 · 无重复无遗漏'}
          </div>
        </div>
      </div>

      {!detail.empty && detail.batches.length > 1 ? (
        <div className="filters" role="group" aria-label="按桶筛选">
          <button type="button" className={batchFilter === null ? 'chip chip-active' : 'chip'} onClick={() => setBatchFilter(null)}>
            全部桶
          </button>
          {detail.batches.map((batch) => (
            <button
              type="button"
              key={batch.batch}
              className={batchFilter === batch.batch ? 'chip chip-active' : 'chip'}
              onClick={() => setBatchFilter(batch.batch)}
            >
              桶 {batch.batch + 1}
            </button>
          ))}
        </div>
      ) : null}

      <div className="bucket-list">
        {batches.map((batch) => {
          const leftRows = detail.left.buckets.filter((bucket) => bucket.batch === batch.batch)
          const rightRows = detail.right.buckets.filter((bucket) => bucket.batch === batch.batch)
          return (
            <details key={batch.batch} open className="bucket-card">
              <summary>
                <span className="bucket-name">桶 {batch.batch + 1}</span>
                <span className="bucket-stats tabular">
                  左 {formatInt(batch.leftRows)} · 右 {formatInt(batch.rightRows)} · 关联 {formatInt(batch.joinRows)}
                </span>
              </summary>
              <div className="bucket-body">
                <div className="bucket-side">
                  <div className="bucket-side-title">
                    {detail.empty ? '空键写入位置（不参与盐桶散列）' : '左侧如何分桶（按行编号散列）'}
                  </div>
                  {leftRows.length === 0 ? (
                    <p className="muted small">本桶没有该键的左记录。</p>
                  ) : (
                    leftRows.map((bucket) => (
                      <div key={`${bucket.file}-${bucket.batch}-${bucket.ordinal}`} className="file-line">
                        <span className="mono small">{bucket.file}</span>
                        <span className="tabular">{formatInt(bucket.rows)} 行</span>
                        <span className="muted small">行编号 {formatRange(bucket.rowIds)}</span>
                      </div>
                    ))
                  )}
                </div>
                <div className="bucket-side">
                  <div className="bucket-side-title">
                    {detail.empty ? '右侧对应文件' : '右侧复制到本桶'}
                  </div>
                  {rightRows.length === 0 ? (
                    <p className="muted small">本桶没有该键的右记录。</p>
                  ) : (
                    rightRows.map((bucket) => (
                      <div key={`${bucket.file}-${bucket.batch}-${bucket.ordinal}`} className="file-line">
                        <span className="mono small">{bucket.file}</span>
                        <span className="tabular">
                          {formatInt(bucket.rows)} {detail.empty ? '行' : '行副本'}
                        </span>
                        <span className="muted small">源行编号 {formatRange(bucket.rowIds)}</span>
                      </div>
                    ))
                  )}
                </div>
              </div>
            </details>
          )
        })}
      </div>

      {totalLeft > 0 && !detail.empty ? (
        <div className="bucket-share">
          <div className="bucket-share-title">左记录在各桶的分布（按行编号散列）</div>
          <div className="share-track" role="img" aria-label={`左记录分到 ${detail.batches.length} 个桶`}>
            {detail.batches.map((batch) => {
              const share = batch.leftRows / totalLeft
              return (
                <div
                  key={batch.batch}
                  className="share-seg"
                  style={{ width: `${share * 100}%`, background: 'var(--series-1)' }}
                  title={`桶 ${batch.batch + 1}: ${formatInt(batch.leftRows)} 行 (${(share * 100).toFixed(0)}%)`}
                >
                  {share > 0.12 ? `桶${batch.batch + 1} · ${(share * 100).toFixed(0)}%` : ''}
                </div>
              )
            })}
          </div>
          <p className="muted small">
            {written === 0 ? '无额外写入' : `左表额外写入 ${formatInt(written)} 行`} · 右表复制 {formatInt(copied)} 行
          </p>
        </div>
      ) : null}
    </div>
  )
}

function formatRange(ids: number[]): string {
  if (ids.length === 0) return '—'
  if (ids.length <= 6) return ids.join(', ')
  const head = ids.slice(0, 3).join(', ')
  const tail = ids.slice(-2).join(', ')
  return `${head} … ${tail}（共 ${formatInt(ids.length)} 行）`
}
