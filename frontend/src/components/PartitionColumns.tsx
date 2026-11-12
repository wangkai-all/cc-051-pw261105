import { useId, useState } from 'react'
import type { Trial } from '../types'
import { formatInt } from '../api'
import { niceTicks } from '../format'

/**
 * Horizontal columns comparing the largest unsalted partition with the salted
 * buckets of the current plan. One series → no legend; direct labels on caps.
 */

type Column = { label: string; value: number; tone: 'baseline' | 'bucket'; isMax?: boolean }

export function PartitionColumns({ trial, saltCount }: { trial: Trial | null; saltCount?: number }) {
  const [hover, setHover] = useState<number | null>(null)
  const titleId = useId()
  if (!trial) return null

  const columns: Column[] = []
  if (trial.salted) {
    columns.push({
      label: '拆分前最大分区',
      value: trial.maxOrdinaryPartition.rows,
      tone: 'baseline',
    })
    trial.bucketRows.forEach((rows, index) => {
      columns.push({
        label: `桶 ${index + 1}`,
        value: rows,
        tone: 'bucket',
        isMax: trial.maxBucket?.batch === index,
      })
    })
  } else {
    columns.push({ label: '最大分区', value: trial.maxOrdinaryPartition.rows, tone: 'baseline' })
  }
  if (columns.length === 0) return null

  const max = Math.max(...columns.map((column) => column.value), 1)
  const ticks = niceTicks(max, 4)
  const width = 720
  const labelGutter = 168
  const chartWidth = width - labelGutter - 16
  const rowHeight = 30
  const bars = columns.length
  const chartHeight = bars * rowHeight + 26
  const scale = (value: number) => (value / ticks[ticks.length - 1]) * chartWidth

  return (
    <figure style={{ margin: 0 }}>
      <svg
        role="img"
        aria-labelledby={titleId}
        viewBox={`0 0 ${width} ${chartHeight}`}
        style={{ width: '100%', height: 'auto', overflow: 'visible' }}
      >
        <title id={titleId}>
          {trial.salted
            ? `最大分区 ${formatInt(trial.maxOrdinaryPartition.rows)} 行，拆分后最大桶 ${formatInt(trial.maxBucket?.rows ?? 0)} 行`
            : `普通方案最大分区 ${formatInt(trial.maxOrdinaryPartition.rows)} 行`}
        </title>
        {/* Gridlines: hairline, one step off the surface, solid, recessive. */}
        {ticks.map((tick) => (
          <g key={tick}>
            <line
              x1={labelGutter + scale(tick)}
              x2={labelGutter + scale(tick)}
              y1={8}
              y2={chartHeight - 22}
              stroke="var(--grid)"
              strokeWidth={1}
            />
            <text
              x={labelGutter + scale(tick)}
              y={chartHeight - 8}
              textAnchor="middle"
              fill="var(--text-muted)"
              fontSize={11}
              className="tabular"
            >
              {tick === 0 ? '0' : formatInt(tick)}
            </text>
          </g>
        ))}
        <line
          x1={labelGutter}
          x2={labelGutter}
          y1={8}
          y2={chartHeight - 22}
          stroke="var(--baseline)"
          strokeWidth={1}
        />
        {columns.map((column, index) => {
          const y = 10 + index * rowHeight
          const barWidth = Math.max(scale(column.value), column.value > 0 ? 2 : 0)
          const fill = column.tone === 'baseline' ? 'var(--text-muted)' : 'var(--series-1)'
          const labelFits = barWidth > 46
          return (
            <g
              key={`${column.label}-${index}`}
              onMouseEnter={() => setHover(index)}
              onMouseLeave={() => setHover(null)}
              onFocus={() => setHover(index)}
              onBlur={() => setHover(null)}
              tabIndex={0}
              aria-label={`${column.label}: ${formatInt(column.value)} 行`}
              style={{ outline: 'none' }}
            >
              {/* generous hit target: full row */}
              <rect
                x={0}
                y={y - 5}
                width={width}
                height={rowHeight}
                fill="transparent"
                style={{ cursor: 'default' }}
              />
              <text
                x={labelGutter - 10}
                y={y + 13}
                textAnchor="end"
                fontSize={12}
                fill="var(--text-secondary)"
              >
                {column.label}
                {column.isMax ? '（最大）' : ''}
              </text>
              {/* 4px rounded data-end, square at the baseline; 16px thick (≤24) */}
              <path
                d={roundedRightBar(labelGutter, y + 1, barWidth, 16, 4)}
                fill={fill}
                opacity={hover === null || hover === index ? 1 : 0.55}
              />
              {labelFits ? (
                <text
                  x={labelGutter + barWidth + 8}
                  y={y + 13}
                  fontSize={11.5}
                  fill="var(--text-secondary)"
                  className="tabular"
                >
                  {formatInt(column.value)}
                </text>
              ) : null}
            </g>
          )
        })}
      </svg>
      {hover !== null ? (
        <div
          style={{
            fontSize: 12,
            color: 'var(--text-secondary)',
            marginTop: 4,
            minHeight: 18,
          }}
        >
          {columns[hover].label} · {formatInt(columns[hover].value)} 行
          {trial.salted && columns[hover].tone !== 'baseline'
            ? ` · 是拆分前最大分区的 ${(
                (columns[hover].value / trial.maxOrdinaryPartition.rows) * 100
              ).toFixed(0)}%`
            : trial.salted && columns[hover].tone === 'baseline'
              ? ' · 拆分前（hash 分区的单桶上限）'
              : ''}
        </div>
      ) : (
        <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 4, minHeight: 18 }}>
          {trial.salted && saltCount ? `${saltCount} 个盐桶 · ` : '未拆分的普通分区 · '}悬停查看每桶行数
        </div>
      )}
    </figure>
  )
}

function roundedRightBar(x: number, y: number, width: number, height: number, radius: number): string {
  const r = Math.min(radius, width / 2, height / 2)
  return [
    `M ${x} ${y}`,
    `L ${x + width - r} ${y}`,
    `Q ${x + width} ${y} ${x + width} ${y + r}`,
    `L ${x + width} ${y + height - r}`,
    `Q ${x + width} ${y + height} ${x + width - r} ${y + height}`,
    `L ${x} ${y + height}`,
    'Z',
  ].join(' ')
}
