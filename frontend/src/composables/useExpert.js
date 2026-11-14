import { ref, watch } from 'vue'
import { saveUi, useProfile } from './useProfile'
import { pushToast } from './useToasts'

// Копия режима в браузере и чей он: кабинет рисуется сразу в нужном режиме, пока грузится профиль.
const KEY = 'aimarketing-expert'
const OWNER_KEY = 'aimarketing-expert-user'

function read(key) {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function write(key, value) {
  try {
    if (value === null) localStorage.removeItem(key)
    else localStorage.setItem(key, value)
  } catch {
    // Без localStorage режим живёт до перезагрузки.
  }
}

/**
 * Экспертный режим (Настройки → Интерфейс). По умолчанию выключен: в меню и вкладках остаётся то, что нужно
 * каждый день, а ручные инструменты (автопилот, площадки, объявления, списки минус-фраз) приходят как
 * предложения ИИ. Включённый режим открывает их в меню.
 *
 * Выбор хранится в аккаунте (user.ui.expert) и едет за человеком на телефон и другой компьютер.
 */
const expert = ref(read(KEY) === '1')

const { user } = useProfile()

function apply(on) {
  expert.value = !!on
  write(KEY, expert.value ? '1' : '0')
  if (user.value) write(OWNER_KEY, String(user.value.id))
}

export function setExpert(on) {
  apply(on)
  saveUi({ expert: expert.value }).catch((err) => {
    // Здесь режим уже переключён, но при следующей загрузке профиля решит аккаунт — честно говорим об этом.
    if (err?.status !== 401) pushToast('Режим не сохранился в аккаунте — переключите его ещё раз', 'error')
  })
}

// Профиль загрузился или обновился: режим из аккаунта главнее того, что запомнил браузер.
watch(
  user,
  (profile) => {
    if (!profile) return
    const saved = profile.ui?.expert
    if (typeof saved === 'boolean') {
      apply(saved)
      return
    }
    // В аккаунте режим ещё не выбирали. Запомненный в браузере переносим один раз — если его выбирал этот же
    // человек (или до переезда настроек в аккаунт, когда браузер не знал, чей он). Чужой режим не наследуем.
    const owner = read(OWNER_KEY)
    const local = read(KEY)
    if (local === null || (owner !== null && owner !== String(profile.id))) {
      expert.value = false
      write(KEY, null)
      write(OWNER_KEY, null)
      return
    }
    write(OWNER_KEY, String(profile.id))
    // Не дошло до сервера — попробуем снова, когда профиль загрузится в следующий раз.
    saveUi({ expert: local === '1' }).catch(() => {})
  },
  // Сразу, без очереди: кабинет рисуется уже в режиме из аккаунта.
  { immediate: true, flush: 'sync' },
)

export function useExpert() {
  return { expert, setExpert }
}
