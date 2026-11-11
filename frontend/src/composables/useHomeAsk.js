/**
 * Вопрос с главной в чат. Текст лежит в sessionStorage, а не в адресе: он может быть длинным и
 * не должен оставаться в истории браузера. Чат забирает его один раз по ?ask=text.
 */
const KEY = 'aimarketing-home-ask'

export function putHomeQuestion(text) {
  try {
    sessionStorage.setItem(KEY, text)
  } catch {
    // Без хранилища вопрос просто не передастся — чат откроется пустым.
  }
}

export function takeHomeQuestion() {
  try {
    const text = sessionStorage.getItem(KEY) || ''
    sessionStorage.removeItem(KEY)
    return text.trim()
  } catch {
    return ''
  }
}
