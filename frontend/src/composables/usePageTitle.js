import { computed, ref } from 'vue'

/**
 * Заголовок страницы, заданный самой страницей, — поверх названия пункта меню. Главная так пишет в h1
 * приветствие «Добрый день, Антон» вместо «Главная». Владелец — чтобы уходящая страница не стёрла
 * заголовок уже открытой новой.
 */
const current = ref(null)

export const pageTitleOverride = computed(() => current.value?.text || '')

export function setPageTitle(owner, text) {
  if (text) current.value = { owner, text }
  else clearPageTitle(owner)
}

export function clearPageTitle(owner) {
  if (current.value?.owner === owner) current.value = null
}
