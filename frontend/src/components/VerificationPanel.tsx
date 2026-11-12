import type { Job, Trial } from '../types'
import { formatBytes, formatInt, formatPercent } from '../api'

/** Before/after verification: does the partitioned join equal the direct join? */

export function VerificationPanel({
  job,
  baseline,
  trial,
}: {
  job: Job
  baseline: { rows: number; seconds: number } | null
  trial: Trial | null
}) {
  const v = job.verification
  const ordinaryMax = trial?.maxOrdinaryPartition.rows ?? 0
  const afterMax = trial?.maxBucket?.rows ?? 0
  const shrunk = ordinaryMax ? 1 - afterMax / ordinaryMax : 0

  type Status = { ok: boolean; good: string; bad: string }
  const same: Status = { ok: true, good: '一致', bad: '不符' }
  const none: Status = { ok: true, good: '无', bad: '存在' }
  const rows: { label: string; value: string; status?: Status; detail?: string }[] = [
    {
      label: '直接 DuckDB 内连接行数（基准）',
      value: formatInt(v.baselineRows),
      detail: baseline ? `${baseline.seconds.toFixed(3)} s 单次执行` : undefined,
    },
    { label: '逐桶关联结果行数', value: formatInt(v.bucketRows), status: { ...same, ok: v.rowsMatch } },
    { label: '缺失的对', value: formatInt(v.missingPairs), status: { ...none, ok: v.missingPairs === 0 } },
    { label: '多出的对', value: formatInt(v.extraPairs), status: { ...none, ok: v.extraPairs === 0 } },
    {
      label: '重复出现的对',
      value: formatInt(v.duplicatePairs),
      status: { ...none, ok: v.duplicatePairs === 0 },
      detail: '同一 (left_row_id, right_row_id) 只出现一次',
    },
    { label: '空键漏入结果', value: formatInt(v.emptyKeyLeaks), status: { ...none, ok: v.emptyKeyLeaks === 0 } },
    {
      label: '左表写入 / 源行数',
      value: `${formatInt(v.left.rowsWritten)} / ${formatInt(v.left.sourceRows)}`,
      status: { ok: v.left.lostRows === 0, good: '无丢行', bad: '有丢行' },
      detail: v.left.lostRows === 0 ? '源行编号全部落盘' : `丢了 ${formatInt(v.left.lostRows)} 行`,
    },
    {
      label: '右表写入 / 源行数 + 复制',
      value: `${formatInt(v.right.rowsWritten)} / ${formatInt(v.right.sourceRows + v.right.replicatedRows)}`,
      status: { ok: v.right.lostRows === 0, good: '无丢行', bad: '有丢行' },
      detail: `复制了 ${formatInt(v.right.replicatedRows)} 行`,
    },
  ]

  return (
    <div>
      <div className={`verdict ${v.passed ? 'verdict-good' : 'verdict-bad'}`}>
        <span className="verdict-icon" aria-hidden="true">
          {v.passed ? '✓' : '✕'}
        </span>
        <div>
          <strong>{v.passed ? '校验通过：逐桶结果与直接内连接完全一致' : '校验失败：结果与基准不一致'}</strong>
          <div className="muted small">
            结果行数、缺失/多出/重复的对、左右写入行数逐项对齐；空键单独计数，未进入结果。
          </div>
        </div>
      </div>

      <div className="metric-grid">
        {rows.map((row) => (
          <div className="metric" key={row.label}>
            <div className="metric-label">{row.label}</div>
            <div className="metric-value tabular">
              {row.value}
              {row.status ? (
                <span className={row.status.ok ? 'status-good' : 'status-bad'}>
                  {row.status.ok ? `✓ ${row.status.good}` : `✕ ${row.status.bad}`}
                </span>
              ) : null}
            </div>
            {row.detail ? <div className="metric-detail">{row.detail}</div> : null}
          </div>
        ))}
      </div>

      <div className="split-summary">
        <div>
          <div className="metric-label">分区分担（拆分前 → 拆分后最大桶）</div>
          <div className="metric-value tabular">
            {formatInt(ordinaryMax)} → {formatInt(afterMax)}
            <span className="muted small">（缩小 {formatPercent(shrunk, 0)}）</span>
          </div>
        </div>
        <div>
          <div className="metric-label">文件与存储</div>
          <div className="metric-value tabular">
            {formatInt(job.files.length)} 个分区文件 · {formatBytes(job.storage.bucketParquetBytes)}
          </div>
        </div>
        <div>
          <div className="metric-label">写入 / 关联耗时</div>
          <div className="metric-value tabular">
            {job.timing.writeSeconds.toFixed(3)} s / {job.timing.joinSeconds.toFixed(3)} s
          </div>
        </div>
      </div>

      <table className="data-table compact">
        <caption className="sr-only">逐桶执行结果：每桶左右行数与产生的关联行数</caption>
        <thead>
          <tr>
            <th scope="col">桶</th>
            <th scope="col" className="num">左文件</th>
            <th scope="col" className="num">右文件</th>
            <th scope="col" className="num">左行数</th>
            <th scope="col" className="num">右行数</th>
            <th scope="col" className="num">关联行数</th>
          </tr>
        </thead>
        <tbody>
          {job.batches.map((batch) => (
            <tr key={batch.batch}>
              <th scope="row">桶 {batch.batch + 1}</th>
              <td className="num tabular">{batch.leftFiles}</td>
              <td className="num tabular">{batch.rightFiles}</td>
              <td className="num tabular">{formatInt(batch.leftRows)}</td>
              <td className="num tabular">{formatInt(batch.rightRows)}</td>
              <td className="num tabular">{formatInt(batch.joinRows)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {job.batches.length === 1 && !trial?.salted ? (
        <p className="muted small">
          未拆分：{formatInt(job.files.length)} 个 hash 分区文件在同一批内两两对齐关联。
        </p>
      ) : null}
      <p className="muted small">
        基准量：{formatInt(v.baselineRows)} 行；本方案最大桶 {formatInt(afterMax)} 行（未拆分
        {formatInt(ordinaryMax)} 行）。
      </p>
    </div>
  )
}
