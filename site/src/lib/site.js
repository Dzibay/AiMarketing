// Общие данные сайта: адреса кабинета, меню, список возможностей. Адреса страниц — со слэшем на конце.

export const BRAND = 'Толк'
export const SLOGAN = 'Реклама с толком'
export const SITE_URL = 'https://tolk-ads.ru'

// Кабинет (SPA) — обычные ссылки с перезагрузкой.
export const APP = {
  login: '/login',
  register: '/register',
  cabinet: '/cabinet',
  privacy: '/privacy',
  consent: '/consent',
  offer: '/offer',
  cookies: '/cookies',
}

export const FEATURES = [
  { slug: 'ii-pomoshchnik', title: 'ИИ-помощник', text: 'Отвечает на вопросы о ваших кампаниях на понятном языке', icon: 'chat' },
  { slug: 'chistka-poiskovyh-zaprosov', title: 'Чистка запросов', text: 'Находит мусорные запросы и собирает минус-фразы', icon: 'filter' },
  { slug: 'svodka-kabinetov', title: 'Сводка по кабинетам', text: 'Проблемы всех кабинетов и клиентов на одном экране', icon: 'grid' },
  { slug: 'avtopilot', title: 'Автопилот', text: 'Правила, которые работают в заданных вами рамках', icon: 'bolt' },
  { slug: 'perezapusk-kampanij', title: 'Перезапуск и эксперименты', text: 'Копия «выгоревшей» кампании и тест вариантов', icon: 'refresh' },
  { slug: 'analitika', title: 'Аналитика', text: 'Директ и Метрика вместе: срезы, цели, цена заявки', icon: 'chart' },
  { slug: 'audit', title: 'Аудит кампаний', text: 'Где утекает бюджет — за пару минут', icon: 'search' },
  { slug: 'sozdanie-kampanij', title: 'Создание кампаний', text: 'Черновик кампании по сайту: группы, ключи, объявления', icon: 'plus' },
  { slug: 'uvedomleniya-telegram', title: 'Telegram', text: 'Утренняя сводка и тревоги, когда что-то пошло не так', icon: 'send' },
  { slug: 'generaciya-izobrazhenij', title: 'Картинки', text: 'Изображения для объявлений РСЯ', icon: 'image' },
]

export const NAV = [
  { href: '/#vozmozhnosti', label: 'Возможности' },
  { href: '/direktologam/', label: 'Директологам' },
  { href: '/biznesu/', label: 'Бизнесу' },
  { href: '/tarify/', label: 'Тарифы' },
  { href: '/bezopasnost/', label: 'Безопасность' },
]

export const FOOTER = [
  {
    title: 'Продукт',
    links: [
      { href: '/#vozmozhnosti', label: 'Возможности' },
      { href: '/tarify/', label: 'Тарифы' },
      { href: '/bezopasnost/', label: 'Безопасность' },
      { href: '/primery/', label: 'Примеры' },
    ],
  },
  {
    title: 'Кому',
    links: [
      { href: '/direktologam/', label: 'Директологам' },
      { href: '/biznesu/', label: 'Владельцам бизнеса' },
    ],
  },
  {
    title: 'Компания',
    links: [
      { href: '/kontakty/', label: 'Контакты' },
      { href: '/beta/', label: 'Бета-программа' },
      { href: '/bot/', label: 'TolkBot' },
    ],
  },
  {
    title: 'Документы',
    links: [
      { href: APP.offer, label: 'Оферта' },
      { href: APP.privacy, label: 'Политика конфиденциальности' },
      { href: APP.consent, label: 'Согласие на обработку данных' },
      { href: APP.cookies, label: 'Cookies' },
    ],
  },
]
