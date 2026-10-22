const KEY = 'aimarketing-cookies'

export function cookiesAccepted() {
  return localStorage.getItem(KEY) === '1'
}

export function acceptCookies() {
  localStorage.setItem(KEY, '1')
}
