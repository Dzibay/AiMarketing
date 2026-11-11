import { ref } from 'vue'

const KEY = 'aimarketing-expert'

function stored() {
  try {
    return localStorage.getItem(KEY) === '1'
  } catch {
    return false
  }
}

/**
 * Экспертный режим (Настройки → Интерфейс). По умолчанию выключен: в меню и вкладках остаётся то, что нужно
 * каждый день, а ручные инструменты (автопилот, площадки, объявления, списки минус-фраз) приходят как
 * предложения ИИ. Включённый режим открывает их в меню. Выбор хранится в браузере.
 */
const expert = ref(stored())

export function setExpert(on) {
  expert.value = !!on
  try {
    localStorage.setItem(KEY, expert.value ? '1' : '0')
  } catch {
    // Без localStorage режим живёт до перезагрузки.
  }
}

export function useExpert() {
  return { expert, setExpert }
}
