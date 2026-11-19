import { ref } from 'vue'

// Откат прямо из сообщения о результате: сервер возвращает undo_log_id, если действие оставило ровно одну
// запись журнала с откатом (заголовок X-Undo-Log или поле результата фоновой задачи), и undo_log_ids, если
// фоновая задача оставила несколько таких записей — тогда «Отменить» вернёт их все разом. Окно отката одно на
// кабинет — его держит AppShell.
const current = ref(null)

/** target — id записи журнала или { ids: [...] } для нескольких записей разом. */
export function openUndo(target, onDone) {
  current.value = Array.isArray(target?.ids) ? { ids: target.ids, onDone } : { id: target, onDone }
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
  if (id) return { action: { label: 'Отменить', run: () => openUndo(id, onDone) } }
  const ids = res?.undo_log_ids
  if (Array.isArray(ids) && ids.length) return { action: { label: 'Отменить', run: () => openUndo({ ids }, onDone) } }
  return {}
}

export function useUndo() {
  return { current, openUndo, closeUndo }
}
