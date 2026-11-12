import type { Trial } from '../types'
import { formatInt } from '../api'
import { maxBucketOf } from '../format'

/**
 * Salt-count comparison. Emphasis form: the unsalted baseline is a muted
 * reference; each salted candidate is a bar in slot 1, the chosen one in slot 2.
 * A table below carries the exact numbers.
 */

export function SaltComparison({
  entries,
  currentSalt,
  onPick,
}: {
  entries: { salt: number; label: string; trial: Trial }[]
  currentSalt: number
  onPick: (salt: number) => void
}) {
  if (entries.length === 0) return null
  const max = Math.max(...entries.map((entry) => maxBucketOf(entry.trial)), 1)
  const saltedAny = entries.some((entry) => entry.trial.salted)

  return (
    <div>
      {saltedAny ? (
      <svg
        viewBox={`0 0 640 ${entries.length * 34 + 24}`}
        style={{ width: '100%', height: 'auto' }}
        role="img"
        aria-label="不同拆分方案的最大桶行数对比"
      >
        {entries.map((entry, index) => {
          const y = 8 + index * 34
          const value = maxBucketOf(entry.trial)
          const width = Math.max((value / max) * 420, value ? 2 : 0)
          const fill = entry.salt === 0 ? 'var(--text-muted)' : 'var(--series-1)'
          const current = entry.salt !== 0 && entry.salt === currentSalt
          return (
            <g
              key={entry.salt}
              onClick={() => (entry.salt === 0 ? undefined : onPick(entry.salt))}
              style={{ cursor: entry.salt === 0 ? 'default' : 'pointer' }}
              aria-label={`${entry.label}：最大分区/桶 ${formatInt(value)} 行`}
            >
              <text x={0} y={y + 13} fontSize={12} fill="var(--text-secondary)">
                {entry.label}
              </text>
              <rect x={172} y={y + 1} width={width} height={16} rx={4} fill={fill} />
              <text x={180 + width} y={y + 13} fontSize={11.5} fill="var(--text-secondary)" className="tabular">
                {formatInt(value)}
              </text>
              {current ? (
                <text x={536} y={y + 13} fontSize={11} fill="var(--text-muted)">
                  ← 当前
                </text>
              ) : null}
            </g>
          )
        })}
      </svg>
      ) : (
        <p className="muted small">
          未选择热键：没有需要拆分的桶，盐桶数不会改变布局，最大分区仍是{' '}
          {formatInt(max)} 行。回到「01 数据与键分布」勾选热键后再比较。
        </p>
      )}
      {saltedAny ? (
      <table className="data-table compact">
        <caption className="sr-only">拆分方案对比：最大桶、复制量与关联行数</caption>
        <thead>
          <tr>
            <th scope="col">方案</th>
            <th scope="col" className="num">最大分区 / 桶</th>
            <th scope="col" className="num">复制增加行数</th>
            <th scope="col" className="num">写入量倍数</th>
            <th scope="col" className="num">关联行数</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry) => {
            const trial = entry.trial
            const current = entry.salt !== 0 && entry.salt === currentSalt
            return (
              <tr key={entry.salt} className={current ? 'selected' : undefined}>
                <th scope="row">{entry.label}</th>
                <td className="num tabular">{formatInt(maxBucketOf(trial))}</td>
                <td className="num tabular">{formatInt(trial.rowsCopied)}</td>
                <td className="num tabular">×{trial.replicationFactor.toFixed(2)}</td>
                <td className="num tabular">{formatInt(trial.estimatedJoinRows)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
      ) : null}
    </div>
  )
}
