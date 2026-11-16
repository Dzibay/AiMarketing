import { formatRange, num, pct, rub } from '../../../utils/format'

/** Значение метрики блока: деньги приходят в копейках, как в «Отчётах», — и форматируются теми же функциями. */
export function blockValue(value, unit) {
  if (value === null || value === undefined) return '—'
  if (unit === 'kop') return rub(value)
  if (unit === 'pct') return pct(value, 2)
  return num(value)
}

/** «19–25 окт.» и «к 12–18 окт.» — период блока и с чем сравнили. */
export function blockPeriod(block) {
  return formatRange(block.date_from, block.date_to)
}

export function blockVersus(block) {
  return block.prev_from ? `к ${formatRange(block.prev_from, block.prev_to)}` : ''
}

/** Ссылка на кампанию из блока; в общем чате у каждой строки свой проект. */
export function campaignLink(row) {
  return row?.workspace_id && row?.campaign_row_id ? `/w/${row.workspace_id}/campaigns/${row.campaign_row_id}` : ''
}
