<template>
  <!-- Компьютер: кнопка «?» в правом нижнем углу; на узком экране вход — из «Ещё» и меню профиля. -->
  <button
    v-if="!narrow && !supportOpen"
    class="sup-fab"
    type="button"
    :aria-label="fabLabel"
    v-tip.left="fabLabel"
    @click="openSupport"
  >
    <CircleHelp aria-hidden="true" />
    <b v-if="supportUnread" class="sup-fab__n" aria-hidden="true">{{ supportUnread > 9 ? '9+' : supportUnread }}</b>
  </button>

  <Teleport to="body">
    <aside
      v-if="supportOpen && !phone"
      ref="panel"
      class="sup-panel"
      role="dialog"
      aria-label="Поддержка"
      tabindex="-1"
      @keydown.esc="closeSupport"
    >
      <header class="sup-panel__head">
        <div>
          <strong>Поддержка</strong>
          <small>{{ replyNote }}</small>
        </div>
        <button class="dicon" type="button" aria-label="Закрыть" v-tip="'Закрыть'" @click="closeSupport">
          <X aria-hidden="true" />
        </button>
      </header>
      <SupportBody />
    </aside>
  </Teleport>

  <UiSheet :open="supportOpen && phone" title="Поддержка" @close="closeSupport">
    <p class="sup-note">{{ replyNote }}</p>
    <SupportBody />
  </UiSheet>
</template>

<script setup>
import { computed, defineComponent, h, nextTick, onBeforeUnmount, ref, resolveDirective, watch, withDirectives } from 'vue'
import { useRoute } from 'vue-router'
import { CircleHelp, ImagePlus, LoaderCircle, Send, X } from '@lucide/vue'
import { errorText, supportApi } from '../api/web'
import { refreshPending } from '../composables/usePending'
import { useProjectFilter } from '../composables/useProjectFilter'
import { closeSupport, openSupport, setSupportUnread, useSupport } from '../composables/useSupport'
import { useViewport } from '../composables/useViewport'
import { niceQuotes, timeAgo } from '../utils/format'
import UiSheet from './ui/UiSheet.vue'

/**
 * Поддержка: одна переписка пользователя с командой. На компьютере — панель справа, на телефоне — шторка.
 * К сообщению прикладываются страница, проект, тариф и браузер — пользователь видит строку «Приложим: …».
 * Скриншот — файлом или вставкой из буфера; перед отправкой уменьшается в браузере (тело запроса до 8 МБ).
 * Ответ поддержки приходит сюда, в колокольчик и в Telegram, если он привязан, — иначе письмом.
 * Пока виджет открыт, переписка обновляется каждые 20 секунд.
 */
const { supportOpen, supportUnread, supportDraft } = useSupport()
const { phone, narrow } = useViewport()
const { project } = useProjectFilter()
const route = useRoute()

const POLL_MS = 20 * 1000
const MAX_SIDE = 1600
const MAX_BYTES = 2_800_000

const panel = ref(null)
const messages = ref([])
const telegram = ref(false)
const loading = ref(false)
const loadError = ref('')
const text = ref('')
const shot = ref(null) // { dataUrl, preview }
const sending = ref(false)
const sendError = ref('')
const images = ref({}) // id → blob: URL
let timer = null

const fabLabel = computed(() =>
  supportUnread.value ? `Поддержка: новый ответ (${supportUnread.value})` : 'Написать в поддержку',
)
const replyNote = computed(() =>
  telegram.value
    ? 'Ответим здесь — придёт и в колокольчик, и в Telegram'
    : 'Ответим здесь — придёт в колокольчик, а если вас не будет на сайте, то и письмом',
)

const projectName = computed(() => {
  const id = Number(route.params.wsId) || project.value?.id
  return id && project.value?.id === id ? project.value.name : ''
})
const attachLine = computed(() =>
  projectName.value ? `Приложим: страницу и проект «${niceQuotes(projectName.value)}»` : 'Приложим: страницу',
)

/** «Chrome 131 · Windows» — коротко, без полной строки User-Agent. */
function browserName() {
  const ua = navigator.userAgent
  const pick = (re, name) => {
    const m = ua.match(re)
    return m ? `${name} ${m[1]}` : ''
  }
  const browser =
    pick(/YaBrowser\/(\d+)/, 'Яндекс Браузер') ||
    pick(/Edg\/(\d+)/, 'Edge') ||
    pick(/OPR\/(\d+)/, 'Opera') ||
    pick(/Firefox\/(\d+)/, 'Firefox') ||
    pick(/Chrome\/(\d+)/, 'Chrome') ||
    pick(/Version\/(\d+).*Safari/, 'Safari') ||
    'браузер'
  const os = /Android/.test(ua)
    ? 'Android'
    : /iPhone|iPad/.test(ua)
      ? 'iOS'
      : /Windows/.test(ua)
        ? 'Windows'
        : /Mac OS/.test(ua)
          ? 'macOS'
          : /Linux/.test(ua)
            ? 'Linux'
            : ''
  return [browser, os, `${window.innerWidth}×${window.innerHeight}`].filter(Boolean).join(' · ')
}

async function load({ quiet = false } = {}) {
  if (!quiet) loading.value = true
  try {
    const data = await supportApi.thread()
    const grew = data.messages.length !== messages.value.length
    messages.value = data.messages || []
    telegram.value = !!data.telegram
    loadError.value = ''
    if (data.unread) await markRead()
    if (grew) scrollDown()
  } catch (err) {
    if (!quiet) loadError.value = errorText(err)
  } finally {
    loading.value = false
  }
}

async function markRead() {
  try {
    await supportApi.read()
    setSupportUnread(0)
    refreshPending() // запись «Ответ поддержки» в колокольчике тоже прочитана
  } catch {
    // Не страшно: счётчик останется до следующего раза.
  }
}

function scrollDown() {
  nextTick(() => {
    const list = document.querySelector('.sup-list')
    if (list) list.scrollTop = list.scrollHeight
  })
}

async function loadImage(id) {
  if (images.value[id]) return
  images.value = { ...images.value, [id]: '' }
  try {
    const url = await supportApi.imageUrl(id)
    images.value = { ...images.value, [id]: url }
  } catch {
    // Картинка не загрузилась — останется подпись.
  }
}

// --- Скриншот ---

function readAsImage(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file)
    const img = new Image()
    img.onload = () => resolve({ img, url })
    img.onerror = () => {
      URL.revokeObjectURL(url)
      reject(new Error('image'))
    }
    img.src = url
  })
}

/** Уменьшаем в браузере: длинная сторона до 1600 px, JPEG; не влезло в 2,8 МБ — ещё ниже качество. */
async function shrink(file) {
  const { img, url } = await readAsImage(file)
  try {
    const scale = Math.min(1, MAX_SIDE / Math.max(img.naturalWidth, img.naturalHeight))
    const canvas = document.createElement('canvas')
    canvas.width = Math.max(1, Math.round(img.naturalWidth * scale))
    canvas.height = Math.max(1, Math.round(img.naturalHeight * scale))
    const ctx = canvas.getContext('2d')
    ctx.fillStyle = '#fff'
    ctx.fillRect(0, 0, canvas.width, canvas.height)
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
    for (const quality of [0.85, 0.7, 0.55, 0.4]) {
      const dataUrl = canvas.toDataURL('image/jpeg', quality)
      if (dataUrl.length * 0.75 <= MAX_BYTES) return dataUrl
    }
    throw new Error('too-big')
  } finally {
    URL.revokeObjectURL(url)
  }
}

async function attach(file) {
  if (!file) return
  sendError.value = ''
  if (!file.type.startsWith('image/')) {
    sendError.value = 'Приложить можно только картинку'
    return
  }
  try {
    const dataUrl = await shrink(file)
    shot.value = { dataUrl }
  } catch {
    sendError.value = 'Не удалось подготовить скриншот — попробуйте другой файл'
  }
}

function onPaste(event) {
  const item = [...(event.clipboardData?.items || [])].find((i) => i.type.startsWith('image/'))
  if (!item) return
  event.preventDefault()
  attach(item.getAsFile())
}

// --- Отправка ---

async function send() {
  const body = text.value.trim()
  if ((!body && !shot.value) || sending.value) return
  sending.value = true
  sendError.value = ''
  try {
    const wsId = Number(route.params.wsId) || project.value?.id || null
    const data = await supportApi.send({
      text: body,
      image: shot.value?.dataUrl || '',
      context: { page: route.fullPath, workspace_id: wsId, browser: browserName() },
    })
    messages.value = data.messages || []
    telegram.value = !!data.telegram
    text.value = ''
    shot.value = null
    scrollDown()
  } catch (err) {
    sendError.value = errorText(err)
  } finally {
    sending.value = false
  }
}

function onKey(event) {
  // На компьютере Enter отправляет, Shift+Enter — новая строка; на телефоне Enter — новая строка.
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && !phone.value) {
    event.preventDefault()
    send()
  }
}

// --- Открытие ---

// Виджет уже открыт, а из чата пришёл ещё вопрос — тоже в поле, если оно пустое.
watch(supportDraft, (draft) => {
  if (!draft || !supportOpen.value) return
  if (!text.value.trim()) text.value = draft
  supportDraft.value = ''
})

watch(
  supportOpen,
  async (open) => {
    clearInterval(timer)
    timer = null
    if (!open) return
    // Вопрос из чата с ИИ — в поле, если человек ещё ничего не начал писать сам.
    if (supportDraft.value) {
      if (!text.value.trim()) text.value = supportDraft.value
      supportDraft.value = ''
    }
    load()
    timer = setInterval(() => document.visibilityState === 'visible' && load({ quiet: true }), POLL_MS)
    await nextTick()
    panel.value?.querySelector('textarea')?.focus({ preventScroll: true })
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  clearInterval(timer)
  for (const url of Object.values(images.value)) if (url) URL.revokeObjectURL(url)
})

/** Переписка и поле ввода — общие для панели и шторки. */
const SupportBody = defineComponent({
  setup() {
    const fileInput = ref(null)
    return () =>
      h('div', { class: 'sup' }, [
        h('div', { class: 'sup-list', 'aria-live': 'polite' }, renderList()),
        h('form', { class: 'sup-form', onSubmit: (e) => (e.preventDefault(), send()) }, [
          shot.value
            ? h('div', { class: 'sup-shot' }, [
                h('img', { src: shot.value.dataUrl, alt: 'Скриншот к сообщению' }),
                h(
                  'button',
                  { type: 'button', class: 'sup-shot__x', 'aria-label': 'Убрать скриншот', onClick: () => (shot.value = null) },
                  h(X, { 'aria-hidden': 'true' }),
                ),
              ])
            : null,
          h('textarea', {
            value: text.value,
            rows: 3,
            maxlength: 4000,
            placeholder: 'Что случилось или что непонятно? Скриншот можно вставить сюда (Ctrl+V)',
            'aria-label': 'Сообщение в поддержку',
            onInput: (e) => (text.value = e.target.value),
            onKeydown: onKey,
            onPaste,
          }),
          sendError.value ? h('p', { class: 'sup-error', role: 'alert' }, sendError.value) : null,
          h('div', { class: 'sup-form__row' }, [
            h('input', {
              ref: fileInput,
              type: 'file',
              accept: 'image/*',
              hidden: true,
              onChange: (e) => {
                attach(e.target.files?.[0])
                e.target.value = ''
              },
            }),
            // Подсказка — общая v-tip, как у остальных кнопок-значков, а не системный title.
            withDirectives(
              h(
                'button',
                { type: 'button', class: 'dicon', 'aria-label': 'Приложить скриншот', onClick: () => fileInput.value?.click() },
                h(ImagePlus, { 'aria-hidden': 'true' }),
              ),
              [[resolveDirective('tip'), 'Приложить скриншот']],
            ),
            h('small', { class: 'sup-attach' }, attachLine.value),
            h(
              'button',
              {
                type: 'submit',
                class: 'btn btn--accent btn--sm',
                disabled: sending.value || (!text.value.trim() && !shot.value),
              },
              [sending.value ? h(LoaderCircle, { class: 'sup-spin', 'aria-hidden': 'true' }) : h(Send, { 'aria-hidden': 'true' }), 'Отправить'],
            ),
          ]),
        ]),
      ])
  },
})

function renderList() {
  if (loadError.value && !messages.value.length) return [h('p', { class: 'sup-empty is-error' }, loadError.value)]
  if (!messages.value.length) {
    if (loading.value) return [h('p', { class: 'sup-empty' }, h('span', { class: 'spinner', 'aria-hidden': 'true' }))]
    return [
      h('div', { class: 'sup-empty' }, [
        h('strong', 'Напишите, чем помочь'),
        h(
          'p',
          'Опишите, что случилось или что непонятно. Скриншот поможет разобраться быстрее — его можно вставить из буфера или выбрать файлом.',
        ),
      ]),
    ]
  }
  return messages.value.map((m) => {
    if (m.has_image) loadImage(m.id)
    return h('div', { key: m.id, class: ['sup-msg', `is-${m.author}`] }, [
      m.author === 'admin' ? h('small', { class: 'sup-msg__who' }, 'Поддержка') : null,
      m.has_image
        ? images.value[m.id]
          ? h('a', { href: images.value[m.id], target: '_blank', rel: 'noopener', class: 'sup-msg__img' }, h('img', { src: images.value[m.id], alt: 'Скриншот' }))
          : h('span', { class: 'sup-msg__img is-loading' }, 'Скриншот…')
        : null,
      m.body ? h('p', m.body) : null,
      h('small', { class: 'sup-msg__time' }, timeAgo(m.created_at)),
    ])
  })
}
</script>
