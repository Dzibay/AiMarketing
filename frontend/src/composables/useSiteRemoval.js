import { ref } from 'vue'
import { errorText, wsApi } from '../api/web'
import { askConfirm } from './useConfirm'
import { pushToast } from './useToasts'
import { loadWorkspaces } from './useWorkspaces'
import { niceQuotes } from '../utils/format'

/**
 * Удаление сайта из Толка и возврат — одно поведение для списка сайтов, настроек проекта и «Подключений».
 * Сайт — счётчик Метрики: удаление скрывает его и отправляет проект в архив (с флажком — удаляет проект
 * вместе с собранными данными). В Метрике и Директе ничего не меняется.
 */

// Растёт после каждого удаления и возврата: списки сайтов и счётчиков следят за ним и перечитываются —
// «Вернуть» в тосте может сработать уже на другой странице.
export const sitesVersion = ref(0)

function changed(onChange) {
  sitesVersion.value += 1
  onChange?.()
}

function siteLabel(name, counterId) {
  return niceQuotes(name) || `Счётчик ${counterId}`
}

/** Спросить и удалить сайт с проектом. onChange — перечитать свой список. → ответ API или null. */
export async function removeSite({ counterId, name, onChange } = {}) {
  const label = siteLabel(name, counterId)
  const answer = await askConfirm(
    'Проект уйдёт в архив, данные по сайту перестанут загружаться, и сайт не будет предлагаться в «Добавить сайт». ' +
      'В Метрике и Директе ничего не изменится.',
    {
      title: `Удалить «${label}» из Толка?`,
      confirmText: 'Удалить сайт',
      danger: true,
      check: {
        label: 'Удалить и собранные данные',
        hint: 'Чаты, правила, эксперименты и статистика Метрики по сайту — вернуть их будет нельзя. Кампании Директа останутся.',
        confirmText: 'Удалить с данными',
      },
    },
  )
  if (!answer) return null
  try {
    const out = await wsApi.hideSite(counterId, answer.checked)
    await loadWorkspaces(true).catch(() => null)
    changed(onChange)
    if (answer.checked) pushToast(`«${label}» удалён вместе с данными`)
    else
      pushToast(`«${label}» удалён`, 'info', {
        action: { label: 'Вернуть', run: () => restoreSite({ counterId, name: label, onChange }) },
      })
    return out
  } catch (err) {
    pushToast(errorText(err), 'error')
    return null
  }
}

/** Счётчик без проекта из «Добавить сайт»: просто перестать его предлагать. */
export async function dismissCounter({ counterId, name, onChange } = {}) {
  const label = siteLabel(name, counterId)
  const ok = await askConfirm(
    `«${label}» больше не будет предлагаться в «Добавить сайт», данные по нему перестанут загружаться. ` +
      'Вернуть его можно в «Удалённых сайтах». В Метрике ничего не изменится.',
    { title: 'Не показывать этот сайт?', confirmText: 'Не показывать', danger: false },
  )
  if (!ok) return null
  try {
    const out = await wsApi.hideSite(counterId, false)
    changed(onChange)
    pushToast(`«${label}» скрыт`, 'info', {
      action: { label: 'Вернуть', run: () => restoreSite({ counterId, name: label, onChange, quiet: true }) },
    })
    return out
  } catch (err) {
    pushToast(errorText(err), 'error')
    return null
  }
}

/** Вернуть удалённый сайт: проект возвращается из архива (или создаётся заново, если удаляли с данными). */
export async function restoreSite({ counterId, name, onChange, quiet = false } = {}) {
  try {
    const out = await wsApi.restoreSite(counterId)
    await loadWorkspaces(true).catch(() => null)
    changed(onChange)
    if (out.workspace) pushToast(`«${niceQuotes(out.workspace.name) || siteLabel(name, counterId)}» снова в Толке`, 'ok')
    else if (out.note) pushToast(`${out.note}. Сайт вернулся в «Добавить сайт»`, 'warn', { timeout: 8000 })
    else if (!quiet) pushToast('Сайт вернулся в «Добавить сайт»', 'ok')
    return out
  } catch (err) {
    pushToast(errorText(err), 'error')
    return null
  }
}
