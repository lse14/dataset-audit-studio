// The backend serializes naive datetimes that are UTC but carry no suffix, so `new Date(value)`
// would parse them as local time and shift every timestamp by the local UTC offset.
export function parseApiTime(value: string): Date {
  const trimmed = value.trim()
  const normalized = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(trimmed) ? trimmed : `${trimmed}Z`
  return new Date(normalized)
}
