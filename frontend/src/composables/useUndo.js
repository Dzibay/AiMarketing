import { ref } from 'vue'

// Откат прямо из сообщения о результате: сервер возвращает undo_log_id, если действие оставило ровно одну
// запись журнала с откатом (заголовок X-Undo-Log или поле результата фоновой задачи). Окно отката одно на
// кабинет — его держит AppShell.
const current = ref(null)

export function openUndo(logId, onDone) {
  current.value = { id: logId, onDone }
}

export function closeUndo() {
  current.value = null
}

/**
 * Опции для pushToast: кнопка «Отменить», если действие можно откатить одной кнопкой.
 * onDone — что сделать после отката (обычно перезагрузить данные страницы).
 */
export function undoAction(res, onDone) {
  const id = res?.undo_log_id
  return id ? { action: { label: 'Отменить', run: () => openUndo(id, onDone) } } : {}
}

export function useUndo() {
  return { current, openUndo, closeUndo }
}
