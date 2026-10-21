/** 匿名服务记录类型与本地存储 */
export interface ServiceRecord {
  id: string
  time: string
  type: string
  plan: string
  duration: string
  param: string
  status: string
  feeling: string
  abnormal: string
  note: string
}

const STORAGE_KEY = 'heaconRecords'
/** 企业版首次加载时清掉联调阶段写入的匿名记录，之后只保留实机归档 */
const PURGE_MARK = 'heaconRecordsEnterprise'

function purgeLegacyRecords() {
  if (typeof localStorage === 'undefined') return
  if (localStorage.getItem(PURGE_MARK) === '1') return
  localStorage.removeItem(STORAGE_KEY)
  localStorage.setItem(PURGE_MARK, '1')
}

purgeLegacyRecords()

export function loadRecords(): ServiceRecord[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw == null) return []
    const list = JSON.parse(raw) as ServiceRecord[]
    if (!Array.isArray(list)) return []
    return list.filter(
      (record) =>
        record &&
        typeof record === 'object' &&
        typeof record.id === 'string' &&
        typeof record.type === 'string',
    )
  } catch {
    return []
  }
}

export function saveRecords(list: ServiceRecord[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(list.slice(0, 100)))
}

export function createRecordId() {
  const day = new Date().toISOString().slice(0, 10).replaceAll('-', '')
  const tail = String(Date.now()).slice(-5)
  return `SRV-${day}-${tail}`
}
