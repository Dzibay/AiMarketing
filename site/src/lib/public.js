// Браузер: настройки сайта из админки (один запрос на страницу) и невидимая SmartCaptcha.
import { EMPTY_PUBLIC } from './contacts.js'

// Промис на window: скрипт страницы и острова могут получить разные копии модуля — запрос всё равно один.
export function sitePublic() {
  if (!window.__tolkPublic) {
    window.__tolkPublic = fetch('/api/site/public', { headers: { Accept: 'application/json' } })
      .then((r) => (r.ok ? r.json() : EMPTY_PUBLIC))
      .then((d) => ({ ...EMPTY_PUBLIC, ...d }))
      .catch(() => EMPTY_PUBLIC)
  }
  return window.__tolkPublic
}

// --- SmartCaptcha (invisible): ключа нет — возвращаем пустой токен, сервер капчу тоже не проверяет ---

let scriptLoad = null

function loadScript() {
  if (window.smartCaptcha) return Promise.resolve()
  if (!scriptLoad) {
    scriptLoad = new Promise((resolve, reject) => {
      const s = document.createElement('script')
      s.src = 'https://smartcaptcha.yandexcloud.net/captcha.js?render=onload'
      s.defer = true
      s.onload = () => resolve()
      s.onerror = () => {
        scriptLoad = null
        reject(new Error('captcha'))
      }
      document.head.appendChild(s)
    })
  }
  return scriptLoad
}

let widget = null
let box = null
let waiting = null

/** Токен капчи для формы. Пустая строка — капча выключена или не загрузилась (сервер тогда решит сам). */
export async function captchaToken() {
  const { captcha_key: key } = await sitePublic()
  if (!key) return ''
  try {
    await loadScript()
  } catch {
    return ''
  }
  const sc = window.smartCaptcha
  if (widget === null) {
    box = document.createElement('div')
    document.body.appendChild(box)
    widget = sc.render(box, { sitekey: key, invisible: true, hideShield: true, hl: 'ru' })
    sc.subscribe(widget, 'success', (token) => waiting?.resolve(token))
    sc.subscribe(widget, 'challenge-hidden', () => waiting?.resolve(''))
    sc.subscribe(widget, 'network-error', () => waiting?.resolve(''))
  } else {
    sc.reset(widget)
  }
  return new Promise((resolve) => {
    const done = (token) => {
      waiting = null
      resolve(token)
    }
    waiting = { resolve: done }
    sc.execute(widget)
  })
}
