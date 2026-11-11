// Доступ к коллекциям с общими правилами: черновики блога видны только в dev.
import { getCollection } from 'astro:content'

export const showDrafts = import.meta.env.DEV

export const blogPosts = async () =>
  (await getCollection('blog', (p) => showDrafts || !p.data.draft)).sort((a, b) => b.data.published - a.data.published)

export const terms = async () => (await getCollection('slovar')).sort((a, b) => a.data.term.localeCompare(b.data.term, 'ru'))

export const helpPages = async () => (await getCollection('spravka')).sort((a, b) => a.data.order - b.data.order)

export const BLOG_TEMY = {
  byudzhet: 'Бюджет и цена заявки',
  minus: 'Минус-слова и запросы',
  analitika: 'Аналитика',
  ii: 'ИИ в рекламе',
}

export const fmtDate = (d) => d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' })
