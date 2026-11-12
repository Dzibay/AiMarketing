import { askConfirm } from '../composables/useConfirm'
import { plural } from './format'

/** Подтверждение перед остановкой или запуском одной кампании — с её названием и тем, что изменится в Директе. */
export function confirmCampaignToggle(c) {
  const on = c.state === 'ON'
  return askConfirm(
    on
      ? `Остановить «${c.name}»? Показы в Директе прекратятся, пока вы не запустите её снова.`
      : `Запустить «${c.name}»? Директ возобновит показы, и бюджет начнёт тратиться.`,
    { title: on ? 'Остановить кампанию' : 'Запустить кампанию' },
  )
}

/** То же для нескольких выбранных: action — pause или resume. */
export function confirmCampaignsBulk(action, n) {
  const what = `${n} ${plural(n, 'кампанию', 'кампании', 'кампаний')}`
  return askConfirm(
    action === 'pause'
      ? `Остановить ${what}? Показы в Директе прекратятся.`
      : `Запустить ${what}? Директ возобновит показы, и бюджет начнёт тратиться. Черновики пропустим.`,
    { title: action === 'pause' ? 'Остановить кампании' : 'Запустить кампании' },
  )
}

/** Отправка одной кампании в архив. */
export function confirmCampaignArchive(c) {
  return askConfirm(`Отправить «${c.name}» в архив? Вернуть её можно из фильтра «Архив».`, { title: 'В архив' })
}
