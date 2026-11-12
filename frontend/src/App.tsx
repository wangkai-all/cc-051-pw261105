import { useCallback, useDeferredValue, useEffect, useState } from 'react'
import { api, formatInt } from './api'
import type { Baseline, CompareEntry, Job, KeyDetail, KeysResponse, Metadata, ResultSample, Trial } from './types'
import { KeyTable } from './components/KeyTable'
import { PartitionColumns } from './components/PartitionColumns'
import { StatTiles } from './components/StatTiles'
import { SaltComparison } from './components/SaltComparison'
import { VerificationPanel } from './components/VerificationPanel'
import { KeyDetailPanel } from './components/KeyDetailPanel'
import { DataPreview, HotKeyCallout, ResultPreview } from './components/DataPreview'

type Tab = 'plan' | 'result'

export function App() {
  const [meta, setMeta] = useState<Metadata | null>(null)
  const [keys, setKeys] = useState<KeysResponse | null>(null)
  const [search, setSearch] = useState('')
  const deferredSearch = useDeferredValue(search)
  const [sort, setSort] = useState('hot')
  const [partitions, setPartitions] = useState(8)
  const [salt, setSalt] = useState(4)
  const [selected, setSelected] = useState<string[]>(['HOT-VIP'])
  const [trial, setTrial] = useState<Trial | null>(null)
  const [comparisons, setComparisons] = useState<CompareEntry[]>([])
  const [job, setJob] = useState<Job | null>(null)
  const [baseline, setBaseline] = useState<Baseline | null>(null)
  const [sample, setSample] = useState<ResultSample | null>(null)
  const [detail, setDetail] = useState<KeyDetail | null>(null)
  const [detailKey, setDetailKey] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('plan')
  const [busy, setBusy] = useState<'plan' | 'execute' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [uploadOpen, setUploadOpen] = useState(false)

  // Load the sample dataset on first mount.
  useEffect(() => {
    let active = true
    ;(async () => {
      try {
        let listing = await api.datasets()
        if (listing.datasets.length === 0) {
          await api.createSample()
          listing = await api.datasets()
        }
        const first = listing.datasets.find((item) => item.id === 'sample') ?? listing.datasets[0]
        const loaded = await api.dataset(first.id)
        if (!active) return
        setMeta(loaded)
        setKeys(await api.keys(loaded.id, '', 200, 'hot'))
      } catch (loadError) {
        if (active) setError(String(loadError))
      }
    })()
    return () => {
      active = false
    }
  }, [])

  useEffect(() => {
    if (!meta) return
    let active = true
    api
      .keys(meta.id, deferredSearch, 200, sort)
      .then((response) => {
        if (active) setKeys(response)
      })
      .catch((keysError) => {
        if (active) setError(String(keysError))
      })
    return () => {
      active = false
    }
  }, [meta, deferredSearch, sort])

  const runTrial = useCallback(
    async (nextSalt: number, nextKeys: string[] = selected) => {
      if (!meta) return
      setBusy('plan')
      setError(null)
      try {
        const [planned, compared] = await Promise.all([
          api.plan({ datasetId: meta.id, partitions, salt: nextSalt, hotKeys: nextKeys }),
          api.compare({ datasetId: meta.id, partitions, hotKeys: nextKeys, salts: [2, 4, 8, 16] }),
        ])
        setTrial(planned)
        setComparisons(compared.comparisons)
      } catch (planError) {
        setError(String(planError))
        setTrial(null)
      } finally {
        setBusy(null)
      }
    },
    [meta, partitions, selected],
  )

  // Trial recomputes whenever the plan inputs change.
  useEffect(() => {
    if (!meta) return
    void runTrial(salt)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meta, partitions, salt, selected.join('|')])

  const toggleKey = (key: string) => {
    setSelected((current) =>
      current.includes(key) ? current.filter((item) => item !== key) : [...current, key],
    )
    setDetailKey(null)
    setDetail(null)
  }

  const execute = async () => {
    if (!meta) return
    setBusy('execute')
    setError(null)
    try {
      const executed = await api.execute({ datasetId: meta.id, partitions, salt, hotKeys: selected })
      setJob(executed)
      setTab('plan')
      const [resultSample, direct] = await Promise.all([
        api.resultSample(executed.id, 20),
        api.baseline(executed.id, 20),
      ])
      setSample(resultSample)
      setBaseline(direct)
      if (selected.length > 0) {
        openDetail(executed.id, selected[0])
      }
    } catch (executeError) {
      setError(String(executeError))
    } finally {
      setBusy(null)
    }
  }

  const openDetail = async (jobId: string, key: string) => {
    setDetailKey(key)
    setDetail(null)
    try {
      setDetail(await api.keyDetail(jobId, key))
    } catch (detailError) {
      setError(String(detailError))
    }
  }

  const isHot = (key: string) => (keys?.keys ?? []).some((row) => row.key === key && row.hot)

  return (
    <div className="app-shell">
      <header className="app-header">
        <div>
          <div className="eyebrow">DuckDB · Parquet · 盐桶拆分</div>
          <h1>热键分区实验室</h1>
          <p className="muted">
            找出把分区挤满的少数键，比较拆分方案，写出分区 Parquet 后逐桶关联，并与直接内连接逐对校验。
          </p>
        </div>
        <div className="header-actions">
          <button type="button" className="button ghost" onClick={() => setUploadOpen((value) => !value)}>
            导入自己的数据
          </button>
          {meta ? (
            <div className="dataset-badge">
              <span className="muted small">当前数据集</span>
              <strong>{meta.id === 'sample' ? '内置样例' : meta.name}</strong>
            </div>
          ) : null}
        </div>
      </header>

      {uploadOpen ? <UploadPanel onLoaded={(loaded) => {
        setMeta(loaded)
        setJob(null)
        setTrial(null)
        setSelected([])
        void api.keys(loaded.id, '', 200, 'hot').then(setKeys)
        setUploadOpen(false)
      }} onError={setError} /> : null}

      {error ? (
        <div className="error-banner" role="alert">
          <strong>出错了：</strong> {error}
        </div>
      ) : null}

      {meta ? (
        <>
          <section className="card section">
            <SectionHead
              index="01"
              title="数据与键分布"
              subtitle={`左右两侧行编号唯一且稳定；业务内容可以重复。空键按 SQL 规则不匹配，单独统计。`}
            />
            <div className="dataset-summary">
              <span>
                左表 <strong className="tabular">{formatInt(meta.profile.left.rows)}</strong> 行
              </span>
              <span>
                右表 <strong className="tabular">{formatInt(meta.profile.right.rows)}</strong> 行
              </span>
              <span>
                可关联行数 <strong className="tabular">{formatInt(meta.profile.keys.matchablePairs)}</strong>
              </span>
              <span>
                热键 <strong className="tabular">{formatInt(meta.profile.keys.hotCount)}</strong> 个
              </span>
              <span className="muted small">阈值：单侧频次 ≥ {meta.profile.keys.threshold}</span>
            </div>
            <HotKeyCallout
              hotKeys={meta.profile.hotKeys.map((row) => ({ ...row, hotBy: 'left' }))}
              onPick={toggleKey}
              selected={selected}
            />
            {keys ? (
              <KeyTable
                rows={keys.keys}
                selected={selected}
                onToggle={toggleKey}
                search={search}
                onSearch={setSearch}
                empty={keys.empty}
                threshold={keys.threshold}
                sort={sort}
                onSort={setSort}
              />
            ) : null}
            <div className="preview-block">
              <DataPreview meta={meta} />
            </div>
          </section>

          <section className="card section">
            <SectionHead
              index="02"
              title="试算与拆分方案"
              subtitle="左侧热键记录按 row_id 散列到盐桶；右侧同键记录复制到每个桶，桶 b 只与桶 b 关联，所以每对原本可关联的记录恰好出现一次。"
            />
            <div className="controls">
              <label>
                <span>分区数 P</span>
                <input
                  type="range"
                  min={2}
                  max={16}
                  value={partitions}
                  onChange={(event) => setPartitions(Number(event.target.value))}
                />
                <strong className="tabular">{partitions}</strong>
              </label>
              <label>
                <span>盐桶数 S</span>
                <input
                  type="range"
                  min={2}
                  max={16}
                  value={salt}
                  onChange={(event) => setSalt(Number(event.target.value))}
                />
                <strong className="tabular">{salt}</strong>
              </label>
              <div className="selected-keys">
                <span className="muted small">拆分的热键</span>
                {selected.length === 0 ? <span className="badge badge-quiet">未选择</span> : selected.map((key) => (
                  <button type="button" key={key} className="chip chip-active" onClick={() => toggleKey(key)}>
                    <span className="mono">{key}</span>
                    {isHot(key) ? <span className="dot dot-hot" aria-hidden="true" /> : null}
                  </button>
                ))}
              </div>
              <button type="button" className="button secondary" onClick={() => void runTrial(salt)} disabled={busy !== null}>
                试算
              </button>
            </div>

            {trial ? (
              <>
                <StatTiles trial={trial} />
                <div className="charts-grid">
                  <div>
                    <div className="chart-title">最大分区 → 各桶行数</div>
                    <PartitionColumns trial={trial} saltCount={salt} />
                    {trial.salted ? (
                      <p className="muted small">
                        未拆分的 {formatInt(trial.maxOrdinaryPartition.rows)} 行集中在一个 hash 分区；拆分后最大桶
                        {formatInt(trial.maxBucket?.rows ?? 0)} 行。普通键仍按 hash 分区（每桶 {trial.ordinaryPartitionsPerSalt} 个）。
                      </p>
                    ) : (
                      <p className="muted small">未选择热键时只有普通 hash 分区，热键会把单个分区挤满。</p>
                    )}
                  </div>
                  <div>
                    <div className="chart-title">盐桶数怎么选</div>
                    <SaltComparison
                      entries={comparisons}
                      currentSalt={salt}
                      onPick={(nextSalt) => {
                        setSalt(nextSalt || salt)
                        if (nextSalt > 0) void runTrial(nextSalt)
                      }}
                    />
                  </div>
                </div>

                {trial.hotKeys.length > 0 ? (
                <table className="data-table compact">
                  <caption className="sr-only">热键拆分明细：每桶左右行数、复制量与产生的关联行数</caption>
                  <thead>
                    <tr>
                      <th scope="col">热键</th>
                      <th scope="col" className="num">左行数</th>
                      <th scope="col" className="num">右行数</th>
                      <th scope="col">左表分到各桶</th>
                      <th scope="col">右表复制</th>
                      <th scope="col" className="num">复制增加行数</th>
                      <th scope="col" className="num">关联行数（左×右）</th>
                    </tr>
                  </thead>
                  <tbody>
                    {trial.hotKeys.map((item) => (
                      <tr key={item.key}>
                        <th scope="row" className="mono">{item.key}</th>
                        <td className="num tabular">{formatInt(item.leftRows)}</td>
                        <td className="num tabular">{formatInt(item.rightRows)}</td>
                        <td className="tabular">{item.leftByBucket.join(' / ')}</td>
                        <td className="tabular">
                          {item.rightRows} × {item.salt} = {formatInt(item.rowsWritten - item.leftRows)}
                        </td>
                        <td className="num tabular">{formatInt(item.rowsCopied)}</td>
                        <td className="num tabular">{formatInt(item.outputRows)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                ) : null}

                <div className="execute-bar">
                  <div>
                    <div className="chart-title">确认后生成分区 Parquet 并逐桶关联</div>
                    <p className="muted small">
                      写入 {formatInt(trial.rowsWritten)} 行（源数据 {formatInt(trial.rowsRead)} 行，复制增加
                      {formatInt(trial.rowsCopied)} 行）。
                      {trial.salted
                        ? `共 ${trial.bucketCount} 个桶 × ${trial.bucketFiles[0]?.ordinaryPartitions ?? 0} 个普通分区。`
                        : `共 ${trial.ordinaryPartitionsPerSalt} 个普通 hash 分区。`}
                    </p>
                  </div>
                  <button type="button" className="button primary" onClick={() => void execute()} disabled={busy !== null}>
                    {busy === 'execute' ? '正在生成分区并关联…' : '执行并校验'}
                  </button>
                </div>
              </>
            ) : (
              <p className="muted">选择至少一个热键后试算；也可以不选热键，查看普通方案的偏斜。</p>
            )}
          </section>

          {job ? (
            <section className="card section">
              <SectionHead
                index="03"
                title="执行结果与逐对校验"
                subtitle="逐桶关联的结果与 DuckDB 直接内连接按 (left_row_id, right_row_id) 逐对比较，重复出现次数也一致。"
              />
              <div className="tab-row">
                <button type="button" className={tab === 'plan' ? 'tab tab-active' : 'tab'} onClick={() => setTab('plan')}>
                  校验概览与桶明细
                </button>
                <button type="button" className={tab === 'result' ? 'tab tab-active' : 'tab'} onClick={() => setTab('result')}>
                  关联结果样例
                </button>
              </div>
              {tab === 'plan' ? (
                <VerificationPanel job={job} baseline={baseline} trial={trial} />
              ) : (
                <ResultPreview job={job} sample={sample} baselineSample={baseline} />
              )}

              <div className="files-block">
                <div className="chart-title">分区文件</div>
                <div className="file-grid">
                  {groupFiles(job.files).map((group) => (
                    <div className="file-group" key={`${group.dataset}-${group.batch}`}>
                      <div className="file-group-title">
                        {group.dataset === 'left' ? '左' : '右'}表 · 桶 {group.batch + 1}
                        <span className="muted small"> · {formatInt(group.rows)} 行</span>
                      </div>
                      {group.files.map((file) => (
                        <div className="file-row small" key={file.file}>
                          <span className="mono">{file.file}</span>
                          <span className="tabular muted">{formatInt(file.rows)} 行</span>
                          <span className="tabular muted">{formatBytes(file.bytes)}</span>
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              </div>

              {selected.length > 0 ? (
                <div className="detail-block">
                  <div className="chart-title">点开热键：左右记录分到了哪些桶、复制了多少、处理前后是否丢行或多行</div>
                  <div className="filters" role="group" aria-label="选择要查看的热键">
                    {selected.map((key) => (
                      <button
                        type="button"
                        key={key}
                        className={detailKey === key ? 'chip chip-active' : 'chip'}
                        onClick={() => void openDetail(job.id, key)}
                      >
                        <span className="mono">{key}</span>
                      </button>
                    ))}
                    <button
                      type="button"
                      className={detailKey === '__empty__' ? 'chip chip-active' : 'chip'}
                      onClick={() => void openDetail(job.id, '__empty__')}
                    >
                      空键
                    </button>
                  </div>
                  {detail ? (
                    <KeyDetailPanel detail={detail} />
                  ) : (
                    <p className="muted small">正在读取桶文件…</p>
                  )}
                </div>
              ) : null}
            </section>
          ) : null}
        </>
      ) : (
        <p className="muted">正在载入样例…</p>
      )}
    </div>
  )
}

function SectionHead({ index, title, subtitle }: { index: string; title: string; subtitle: string }) {
  return (
    <div className="section-head">
      <span className="section-index tabular">{index}</span>
      <div>
        <h2>{title}</h2>
        <p className="muted small">{subtitle}</p>
      </div>
    </div>
  )
}

function UploadPanel({ onLoaded, onError }: { onLoaded: (meta: Metadata) => void; onError: (message: string) => void }) {
  const [leftFile, setLeftFile] = useState<File | null>(null)
  const [rightFile, setRightFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  return (
    <section className="card section upload">
      <div className="section-head">
        <span className="section-index tabular">⇪</span>
        <div>
          <h2>导入 Parquet 左右表</h2>
          <p className="muted small">
            必需列：row_id（整数且一侧内唯一）、join_key（字符串，NULL 与 '' 视为空键）；payload 可选。行编号不能重复，业务内容可以重复。
          </p>
        </div>
      </div>
      <div className="upload-row">
        <label>
          <span>左表</span>
          <input type="file" accept=".parquet" onChange={(event) => setLeftFile(event.target.files?.[0] ?? null)} />
        </label>
        <label>
          <span>右表</span>
          <input type="file" accept=".parquet" onChange={(event) => setRightFile(event.target.files?.[0] ?? null)} />
        </label>
        <button
          type="button"
          className="button primary"
          disabled={!leftFile || !rightFile || busy}
          onClick={async () => {
            if (!leftFile || !rightFile) return
            setBusy(true)
            try {
              const loaded = await api.upload(leftFile, rightFile)
              setBusy(false)
              onLoaded(loaded)
            } catch (uploadError) {
              setBusy(false)
              onError(String(uploadError))
            }
          }}
        >
          {busy ? '上传中…' : '上传并建档'}
        </button>
      </div>
    </section>
  )
}

function groupFiles(files: Job['files']): { dataset: string; batch: number; rows: number; files: Job['files'] }[] {
  const groups = new Map<string, { dataset: string; batch: number; rows: number; files: Job['files'] }>()
  for (const file of files) {
    const id = `${file.dataset}-${file.batch}`
    const group = groups.get(id) ?? { dataset: file.dataset, batch: file.batch, rows: 0, files: [] }
    group.rows += file.rows
    group.files.push(file)
    groups.set(id, group)
  }
  return Array.from(groups.values())
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / (1024 * 1024)).toFixed(2)} MB`
}
