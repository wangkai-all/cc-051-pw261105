import type { Trial } from '../types'
import { formatBytes, formatInt, formatPercent } from '../api'

/**
 * KPI row: the trial numbers a reader decides from. Values are proportional
 * figures (not tabular) at display size; labels in secondary ink.
 */

function Tile({
  label,
  value,
  detail,
  tone,
}: {
  label: string
  value: string
  detail?: React.ReactNode
  tone?: 'neutral' | 'good' | 'warning'
}) {
  return (
    <div className="tile">
      <div className="tile-label">{label}</div>
      <div className="tile-value" style={tone === 'good' ? { color: 'var(--good-text)' } : undefined}>
        {value}
      </div>
      {detail ? <div className="tile-detail">{detail}</div> : null}
    </div>
  )
}

export function StatTiles({ trial, baseline }: { trial: Trial | null; baseline?: Trial | null }) {
  if (!trial) {
    return (
      <div className="tile-row">
        <Tile label="最大桶" value="—" detail="先选择热键并试算" />
      </div>
    )
  }
  const maxBucket = trial.maxBucket?.rows ?? 0
  const ordinaryMax = trial.maxOrdinaryPartition.rows
  const improvement = ordinaryMax ? 1 - maxBucket / ordinaryMax : 0
  const joinRows = trial.estimatedJoinRows
  const emptyTotal = trial.empty.leftRows + trial.empty.rightRows
  const joinable = trial.rowsRead - emptyTotal
  const copyShare = trial.rowsWritten ? trial.rowsCopied / trial.rowsWritten : 0

  return (
    <div className="tile-row">
      <Tile
        label={trial.salted ? '拆分后最大桶' : '最大分区'}
        value={formatInt(maxBucket || ordinaryMax)}
        tone={trial.salted && improvement > 0 ? 'good' : 'neutral'}
        detail={
          trial.salted ? (
            <>
              拆分前 {formatInt(ordinaryMax)} 行 · 缩小 {formatPercent(improvement, 0)}
            </>
          ) : (
            <>未拆分 · hash 分区单桶上限</>
          )
        }
      />
      <Tile
        label="复制增加的存储"
        value={formatBytes(trial.bytesCopied)}
        detail={
          <>
            右表复制 {formatInt(trial.rowsCopied)} 行 · 写入量 ×{trial.replicationFactor.toFixed(2)}
          </>
        }
      />
      <Tile
        label="关联行数（左×右）"
        value={formatInt(joinRows)}
        detail={
          <>
            各键左×右求和 · 左右非空 {formatInt(joinable)} 行（不含空键）
          </>
        }
      />
      <Tile
        label="空键（不匹配）"
        value={formatInt(emptyTotal)}
        detail={
          <>
            左 {formatInt(trial.empty.leftRows)} · 右 {formatInt(trial.empty.rightRows)} · 关联前排除
          </>
        }
      />
      {trial.salted ? (
        <Tile
          label="复制行占写入量"
          value={formatPercent(copyShare, 0)}
          detail={
            <>
              写入 {formatInt(trial.rowsWritten)} 行 · 其中复制 {formatInt(trial.rowsCopied)} 行 · {trial.bucketCount} 个桶
            </>
          }
        />
      ) : (
        <Tile
          label="普通分区数"
          value={formatInt(trial.ordinaryPartitionsPerSalt)}
          detail={<>按 hash 分键 · 热键集中在一个分区</>}
        />
      )}
      {baseline ? (
        <Tile
          label="普通方案（对照）"
          value={formatInt(baseline.maxOrdinaryPartition.rows)}
          detail={<>最大分区行数 · 巨大热键集中在一桶</>}
        />
      ) : null}
    </div>
  )
}
