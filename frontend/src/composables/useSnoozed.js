import { ref, watch } from 'vue'

const SEVERITY_RANK = { info: 0, warning: 1, critical: 2 }
const rank = (severity) => SEVERITY_RANK[severity] ?? 0

/**
 * Только что скрытые через «⋯» сигналы: убираем их с экрана сразу, не дожидаясь, пока сводка перечитается.
 * source — геттер списка проблем: пришла новая сводка — сервер уже сам их не отдаёт, список сбрасываем.
 */
export function useSnoozed(source) {
  const gone = ref([])
  watch(source, () => {
    gone.value = []
  })

  /**
   * g — то, что прислал SnoozeMenu: { key, mode, kind, scope, severity }. «Весь тип» прячет такие сигналы
   * клиента, но не серьёзнее скрытого — как сервер (snoozes.type_worse): критичное остаётся на экране.
   */
  function mark(g) {
    gone.value = [...gone.value, g]
  }

  function isGone(p) {
    return gone.value.some((g) =>
      g.mode === 'type'
        ? p.kind === g.kind && p.type_scope?.key === g.scope && rank(p.severity) <= rank(g.severity)
        : p.key === g.key,
    )
  }

  return { mark, isGone }
}
