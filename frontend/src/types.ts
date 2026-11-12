export type KeyRow = {
  key: string
  leftRows: number
  rightRows: number
  pairs: number
  hot?: boolean
}

export type SideStats = {
  rows: number
  emptyRows: number
  emptyNullRows: number
  distinctKeys: number
  bytes: number
}

export type Profile = SideStats
export type Metadata = {
  id: string
  name: string
  createdAt: number
  profile: {
    left: SideStats
    right: SideStats
    keys: {
      distinct: number
      onlyLeft: number
      onlyRight: number
      matchablePairs: number
      hotCount: number
      threshold: number
    }
    hotKeys: KeyRow[]
    topKeys: KeyRow[]
    preview: { left: PreviewRow[]; right: PreviewRow[] }
  }
}

export type PreviewRow = {
  rowId: number
  joinKey: string | null
  isNull: boolean
  payload: string
}

export type BucketFileRow = {
  batch: number
  saltedLeftRows: number
  saltedRightRows: number
  ordinaryLeftRows: number
  ordinaryRightRows: number
  ordinaryPartitions: number
  leftRows: number
  rightRows: number
  rows: number
}

export type HotKeyPlanJson = {
  key: string
  leftRows: number
  rightRows: number
  salt: number
  leftByBucket: number[]
  rightByBucket: number[]
  rowsCopied: number
  rowsWritten: number
  outputRows: number
}

export type Trial = {
  partitions: number
  salt: number
  salted: boolean
  ordinaryPartitionsPerSalt: number
  bucketCount: number
  bucketRows: number[]
  bucketLeftRows: number[]
  bucketRightRows: number[]
  bucketFiles: BucketFileRow[]
  maxBucket: { batch: number; leftRows: number; rightRows: number; rows: number } | null
  maxOrdinaryPartition: {
    partition: number
    leftRows: number
    rightRows: number
    rows: number
    keys: string[]
  }
  rowsRead: number
  rowsWritten: number
  rowsCopied: number
  bytesCopied: number
  bytePerRow: number
  replicationFactor: number
  estimatedJoinRows: number
  saltedOutputRows: number
  hotKeys: HotKeyPlanJson[]
  empty: {
    leftRows: number
    rightRows: number
    leftNullRows: number
    rightNullRows: number
    excludedFromJoin: boolean
  }
  plan: { partitions: number; salt: number; ordinaryPartitionsPerSalt: number; hotKeys: HotKeyPlanJson[] }
}

export type JobFile = {
  dataset: 'left' | 'right'
  batch: number
  scope: 'salted' | 'ordinary'
  ordinal: number
  file: string
  rows: number
  bytes: number
}

export type Verification = {
  passed: boolean
  baselineRows: number
  bucketRows: number
  rowsMatch: boolean
  missingPairs: number
  extraPairs: number
  duplicatePairs: number
  emptyKeyLeaks: number
  left: { sourceRows: number; rowsWritten: number; lostRows: number }
  right: { sourceRows: number; rowsWritten: number; replicatedRows: number; lostRows: number }
  empty: { leftRows: number; rightRows: number; excludedFromJoin: boolean }
}

export type Job = {
  id: string
  datasetId: string
  createdAt: number
  elapsedSeconds: number
  plan: Trial
  files: JobFile[]
  batches: { batch: number; leftFiles: number; rightFiles: number; leftRows: number; rightRows: number; joinRows: number }[]
  result: { rows: number; path: string; resultParquetBytes: number }
  verification: Verification
  storage: { bucketParquetBytes: number; sourceParquetBytes: number }
  timing: { writeSeconds: number; joinSeconds: number }
}

export type KeyPlacement = {
  batch: number
  scope: 'salted' | 'ordinary'
  ordinal: number | null
  file: string
  rows: number
  rowIds: number[]
}

export type KeyDetail = {
  key: string
  keyLabel: string
  empty: boolean
  mode: 'salted' | 'ordinary'
  batches: { batch: number; leftRows: number; rightRows: number; joinRows: number; scopes: string[] }[]
  left: {
    sourceRows: number
    rowsWritten: number
    lostRows: number
    duplicateWrites: number
    payloadCount: number
    buckets: KeyPlacement[]
    rowIds: number[]
  }
  right: {
    sourceRows: number
    rowsWritten: number
    copiesWritten: number
    payloadCount: number
    buckets: KeyPlacement[]
    sampleRowIds: number[]
  }
  join: {
    rows: number
    expectedRows: number
    matchesLeftTimesRight: boolean
    duplicatesPerPair: number | null
    perBatch: { batch: number; leftRows: number; rightRows: number; joinRows: number; scopes: string[] }[]
  }
  note: string | null
}

export type Baseline = {
  rows: number
  seconds: number
  sample: { leftRowId: number; rightRowId: number; joinKey: string; leftPayload: string; rightPayload: string }[]
}

export type ResultSample = {
  rows: { leftRowId: number; rightRowId: number; joinKey: string; leftPayload: string; rightPayload: string }[]
  total: number
}

export type KeysResponse = {
  keys: KeyRow[]
  total: number
  limit: number
  empty: { leftRows: number; rightRows: number; note: string }
  threshold: number
}

export type CompareEntry = { salt: number; label: string; trial: Trial }
