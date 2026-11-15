import { computed } from 'vue'
import { saveUi, useProfile } from './useProfile'
import { pushToast } from './useToasts'

/**
 * Роль пользователя (users.ui.role): owner — владелец бизнеса, pro — специалист по рекламе.
 * Выбирают на первом шаге мастера или в карточке «Как вам удобнее?» на главной.
 *
 * От роли зависят режим (владельцу — простой, специалисту — экспертный), быстрые вопросы на главной и набор
 * готовых задач, подсказки и тон ИИ (роль уходит в системную подсказку на сервере). Режим потом можно
 * переключить отдельно — роль от этого не меняется.
 */
export const ROLES = {
  owner: {
    title: 'Я владелец бизнеса',
    text: 'Простые слова и только главное: сколько потратили, сколько заявок и что сделать.',
  },
  pro: {
    title: 'Я веду рекламу',
    text: 'Термины и цифры, все отчёты и ручные инструменты Директа — сразу в экспертном режиме.',
  },
}

const { user } = useProfile()

// Пока роль не выбрана — как у владельца: простой режим включён по умолчанию.
const role = computed(() => (user.value?.ui?.role in ROLES ? user.value.ui.role : 'owner'))
const chosen = computed(() => user.value?.ui?.role in ROLES)

/** Запоминает роль и включает её режим. Режим применяется сразу (useExpert следит за профилем). */
export async function setRole(value) {
  try {
    await saveUi({ role: value, expert: value === 'pro' })
  } catch (err) {
    if (err?.status !== 401) pushToast('Не получилось сохранить выбор — попробуйте ещё раз', 'error')
    throw err
  }
}

export function useRole() {
  return { role, chosen, setRole }
}
