import { ref } from 'vue'

// Одно окно подтверждения на всё приложение (вместо window.confirm): askConfirm(текст) → Promise<boolean>.
const current = ref(null)

// Подпись кнопки по первому глаголу вопроса: «Удалить …?» → «Удалить».
const VERBS = [
  [/^удалить/i, 'Удалить', true],
  [/в архив/i, 'В архив', true],
  [/^отключить/i, 'Отключить', true],
  [/^включить полный доступ/i, 'Включить', true],
  [/^применить/i, 'Применить', true],
  [/^записать в директ/i, 'Записать', false],
]

export function askConfirm(message, { title = 'Подтвердите действие', confirmText, cancelText = 'Отмена', danger } = {}) {
  if (current.value) current.value.resolve(false)
  const text = String(message)
  const verb = VERBS.find(([re]) => re.test(text))
  return new Promise((resolve) => {
    current.value = {
      message: text,
      title,
      confirmText: confirmText || verb?.[1] || 'Да',
      cancelText,
      danger: danger ?? verb?.[2] ?? false,
      resolve: (value) => {
        current.value = null
        resolve(value)
      },
    }
  })
}

// То же окно с полем ввода (вместо window.prompt): askText(подпись) → Promise<string | null>, null — отмена.
export function askText(
  label,
  {
    title = label,
    value = '',
    message = '',
    placeholder = '',
    type = 'text',
    required = false,
    confirmText = 'Сохранить',
    cancelText = 'Отмена',
    danger = false,
  } = {},
) {
  if (current.value) current.value.resolve(false)
  return new Promise((resolve) => {
    current.value = {
      message,
      title,
      confirmText,
      cancelText,
      danger,
      input: { label, value: value == null ? '' : String(value), placeholder, type, required },
      resolve: (ok) => {
        const text = current.value?.input?.value ?? ''
        current.value = null
        resolve(ok ? text : null)
      },
    }
  })
}

export function useConfirm() {
  return { current, askConfirm, askText }
}
