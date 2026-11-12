import type { Baseline, Job, Metadata } from '../types'
import { formatBytes, formatInt } from '../api'
import { keyDisplay } from '../format'

/** Data preview: the sample mix — hot keys, ordinary keys, repeated payloads, empty keys. */

export function DataPreview({ meta }: { meta: Metadata }) {
  const left = meta.profile.preview.left
  const right = meta.profile.preview.right
  return (
    <div className="preview-grid">
      <SidePreview title="左表" rows={left} stats={meta.profile.left} side="left" />
      <SidePreview title="右表" rows={right} stats={meta.profile.right} side="right" />
    </div>
  )
}

function SidePreview({
  title,
  rows,
  stats,
  side,
}: {
  title: string
  rows: { rowId: number; joinKey: string | null; isNull: boolean; payload: string }[]
  stats: { rows: number; emptyRows: number; distinctKeys: number; emptyNullRows: number }
  side: 'left' | 'right'
}) {
  return (
    <div>
      <div className="preview-head">
        <strong>{title}</strong>
        <span className="muted small">
          {formatInt(stats.rows)} 行 · {formatInt(stats.distinctKeys)} 个非空键 · 空键 {formatInt(stats.emptyRows)}（其中 NULL {formatInt(stats.emptyNullRows)}）
        </span>
      </div>
      <table className="data-table compact">
        <thead>
          <tr>
            <th scope="col" className="num">row_id</th>
            <th scope="col">join_key</th>
            <th scope="col">payload（业务内容可重复）</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={`${side}-${row.rowId}`}>
              <td className="num tabular">{row.rowId}</td>
              <td className="mono">
                {row.isNull ? (
                  <span className="badge badge-empty">NULL</span>
                ) : row.joinKey === '' ? (
                  <span className="badge badge-empty">空字符串</span>
                ) : (
                  row.joinKey
                )}
              </td>
              <td>{row.payload}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Join output sample, with a switch to compare against the direct join's sample. */

export function ResultPreview({
  job,
  sample,
  baselineSample,
}: {
  job: Job
  sample: { rows: { leftRowId: number; rightRowId: number; joinKey: string; leftPayload: string; rightPayload: string }[]; total: number } | null
  baselineSample: Baseline | null
}) {
  return (
    <div>
      <div className="preview-head">
        <strong>关联结果</strong>
        <span className="muted small">
          共 {formatInt(job.result.rows)} 行 · 文件 {job.result.path.split('/').slice(-3).join('/')} ·
          Parquet {formatBytes(job.result.resultParquetBytes)}
        </span>
      </div>
      <table className="data-table compact">
        <caption className="sr-only">逐桶关联结果的前 20 行，按 (left_row_id, right_row_id) 排序</caption>
        <thead>
          <tr>
            <th scope="col" className="num">left_row_id</th>
            <th scope="col" className="num">right_row_id</th>
            <th scope="col">join_key</th>
            <th scope="col">left.payload</th>
            <th scope="col">right.payload</th>
          </tr>
        </thead>
        <tbody>
          {(sample?.rows ?? []).map((row) => (
            <tr key={`${row.leftRowId}-${row.rightRowId}`}>
              <td className="num tabular">{row.leftRowId}</td>
              <td className="num tabular">{row.rightRowId}</td>
              <td className="mono">{row.joinKey}</td>
              <td>{row.leftPayload}</td>
              <td>{row.rightPayload}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {baselineSample ? (
        <p className="muted small">
          基准（直接内连接）：{formatInt(baselineSample.rows)} 行，{baselineSample.seconds.toFixed(3)} s；逐桶结果
          {job.verification.rowsMatch ? '行数一致' : '行数不一致'}，重复次数
          {job.verification.duplicatePairs === 0 ? '一致（无重复对）' : `偏差 ${formatInt(job.verification.duplicatePairs)}`}。
        </p>
      ) : null}
    </div>
  )
}


export function HotKeyCallout({ hotKeys, onPick, selected }: { hotKeys: { key: string; leftRows: number; rightRows: number; pairs: number; hotBy: string }[]; onPick: (key: string) => void; selected: string[] }) {
  if (hotKeys.length === 0) return null
  return (
    <div className="hot-callout">
      <div className="hot-callout-title">检测到 {hotKeys.length} 个热键</div>
      <div className="hot-callout-body">
        {hotKeys.map((item) => (
          <button
            key={`att-${item.key}`}
            type="button"
            className={selected.includes(item.key) ? 'hot-pill hot-pill-active' : 'hot-pill'}
            onClick={() => onPick(item.key)}
          >
            <span className="mono">{keyDisplay(item.key)}</span>
            <span className="muted small">左 {formatInt(item.leftRows)} / 右 {formatInt(item.rightRows)}</span>
          </button>
        ))}
      </div>
      <p className="muted small">
        阈值依据：单侧频次 ≥ 20 判为热键。勾选后按行编号分到多个盐桶，右表同键记录复制到每个桶。
      </p>
    </div>
  )
}
