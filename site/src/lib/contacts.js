// Контакты и реквизиты задаются при сборке (site/.env или build-args образа). Пустое значение — строку не выводим.
const env = import.meta.env

export const CONTACTS = {
  email: env.PUBLIC_SUPPORT_EMAIL || '',
  phone: env.PUBLIC_SUPPORT_PHONE || '',
  telegram: (env.PUBLIC_SUPPORT_TELEGRAM || '').replace(/^@/, ''),
  legal: env.PUBLIC_LEGAL_LINE || '',
}

export const phoneHref = (phone) => `tel:${phone.replace(/[^\d+]/g, '')}`
