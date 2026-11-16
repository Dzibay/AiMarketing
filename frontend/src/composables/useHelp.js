import { computed, ref } from 'vue'
import { CHANGELOG } from '../utils/help'
import { saveUi, useProfile } from './useProfile'
import { closeSupport } from './useSupport'

// Боковая панель справки (кнопка «?» у заголовка страницы): какая статья открыта. Пустая строка — закрыта.
const helpSlug = ref('')

export function openHelp(slug) {
  closeSupport() // обе панели живут справа — открыта одна
  helpSlug.value = slug || ''
}

export function closeHelp() {
  helpSlug.value = ''
}

const { user } = useProfile()
const latest = CHANGELOG[0] || null

/**
 * «Что нового»: свежая запись, которую человек ещё не открыл, — точка на колокольчике. Метка — дата записи
 * в users.ui.whats_new_seen. Кто ещё ни одной не открывал, тому новы только записи после его регистрации.
 */
const whatsNewFresh = computed(() => {
  const u = user.value
  if (!u || !latest) return false
  const seen = u.ui?.whats_new_seen
  if (seen) return seen < latest.id
  return String(u.created_at || '').slice(0, 10) < latest.date
})

export function markWhatsNewSeen() {
  if (whatsNewFresh.value) saveUi({ whats_new_seen: latest.id }).catch(() => {})
}

export function useHelp() {
  return { helpSlug, whatsNew: latest, whatsNewFresh }
}
