import { useMemo, useState } from 'react'
import type { KeyRow } from '../types'
import { formatInt } from '../api'
import { keyDisplay } from '../format'

/**
 * Key frequency table. The hot population is the story, so it gets emphasis:
 * hot rows carry a status dot + label (never color alone) and the table view
 * doubles as the chart's accessible data table.
 */
export function KeyTable({
  rows,
  selected,
  onToggle,
  search,
  onSearch,
  empty,
  threshold,
  sort,
  onSort,
}: {
  rows: KeyRow[]
  selected: string[]
  onToggle: (key: string) => void
  search: string
  onSearch: (value: string) => void
  empty: { leftRows: number; rightRows: number; note: string }
  threshold: number
  sort: string
  onSort: (value: string) => void
}) {
  const [showAll, setShowAll] = useState(false)
  const visible = useMemo(() => (showAll ? rows : rows.slice(0, 12)), [rows, showAll])
  const maxRows = Math.max(...rows.map((row) => Math.max(row.leftRows, row.rightRows)), 1)

  return (
    <div>
      <div className="toolbar">
        <input
          type="search"
          value={search}
          placeholder="搜索关联键，例如 HOT、CUST……"
          onChange={(event) => onSearch(event.target.value)}
          aria-label="搜索关联键"
        />
        <select value={sort} onChange={(event) => onSort(event.target.value)} aria-label="排序方式">
          <option value="hot">按单侧最大频次</option>
          <option value="pairs">按关联行数</option>
        </select>
        <span className="muted">
          {formatInt(rows.length)} 个键 · 热键阈值 ≥ {threshold}
        </span>
      </div>

      <table className="data-table">
        <caption className="sr-only">关联键频次：左表行数、右表行数、左右相乘的关联行数</caption>
        <thead>
          <tr>
            <th scope="col">选择</th>
            <th scope="col">关联键</th>
            <th scope="col" className="num">左表行数</th>
            <th scope="col" className="num">右表行数</th>
            <th scope="col" className="num">关联行数（左×右）</th>
            <th scope="col">
              频次对比
              <span className="legend legend-inline" aria-hidden="true">
                <span className="legend-item">
                  <span className="legend-swatch swatch-left" />
                  左表
                </span>
                <span className="legend-item">
                  <span className="legend-swatch swatch-right" />
                  右表
                </span>
              </span>
            </th>
            <th scope="col">判定</th>
          </tr>
        </thead>
        <tbody>
          {visible.map((row) => {
            const isSelected = selected.includes(row.key)
            return (
              <tr key={row.key} className={isSelected ? 'selected' : undefined}>
                <td>
                  <input
                    type="checkbox"
                    checked={isSelected}
                    onChange={() => onToggle(row.key)}
                    aria-label={`把 ${keyDisplay(row.key)} 作为热键拆分`}
                  />
                </td>
                <th scope="row" className="mono">
                  {keyDisplay(row.key)}
                </th>
                <td className="num tabular">{formatInt(row.leftRows)}</td>
                <td className="num tabular">{formatInt(row.rightRows)}</td>
                <td className="num tabular">{formatInt(row.pairs)}</td>
                <td>
                  {/* two thin bars, one per side: identity is by position + a 2px surface gap */}
                  <div className="minibars" aria-hidden="true">
                    <span
                      className="mini mini-left"
                      style={{ width: `${Math.max((row.leftRows / maxRows) * 100, row.leftRows ? 2 : 0)}%` }}
                    />
                    <span
                      className="mini mini-right"
                      style={{ width: `${Math.max((row.rightRows / maxRows) * 100, row.rightRows ? 2 : 0)}%` }}
                    />
                  </div>
                  <span className="sr-only">
                    左 {row.leftRows} 行，右 {row.rightRows} 行
                  </span>
                </td>
                <td>
                  {row.hot ? (
                    <span className="badge badge-hot">
                      <span className="dot dot-hot" aria-hidden="true" />
                      热键
                    </span>
                  ) : (
                    <span className="badge badge-quiet">
                      <span className="dot dot-quiet" aria-hidden="true" />
                      普通
                    </span>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
        <tfoot>
          <tr className="empty-row">
            <td />
            <th scope="row" className="mono">
              &lt;空键&gt;
            </th>
            <td className="num tabular">{formatInt(empty.leftRows)}</td>
            <td className="num tabular">{formatInt(empty.rightRows)}</td>
            <td className="num tabular">0</td>
            <td colSpan={2} className="muted small">
              {empty.note}
            </td>
          </tr>
        </tfoot>
      </table>
      {rows.length > 12 ? (
        <button type="button" className="link-button" onClick={() => setShowAll((value) => !value)}>
          {showAll ? '收起' : `显示全部 ${formatInt(rows.length)} 个键`}
        </button>
      ) : null}
    </div>
  )
}
