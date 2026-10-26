import { inject } from 'vue'

/** Текущее пространство для вкладок: { ws (ref), reload(), patch(fields) }. */
export const WS_KEY = Symbol('workspace')

export function useWs() {
  const ctx = inject(WS_KEY, null)
  if (!ctx) throw new Error('useWs() вне WorkspaceView')
  return ctx
}
