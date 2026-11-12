<template>
  <h2 v-if="!bare" class="section-h">
    Сервис заметил <span class="cgroup__n">{{ visible.length }}</span>
    <small class="section-h__note">по цифрам, без ИИ — лимит не тратится</small>
  </h2>
  <div v-if="visible.length" class="sigs">
    <article v-for="s in visible" :key="s.key" class="sig">
      <div class="sig__top">
        <span class="st" :class="s.severity === 'critical' ? 'st--bad' : 'st--warn'">
          <TriangleAlert :size="14" aria-hidden="true" />
          {{ s.severity === 'critical' ? 'Срочно' : 'Стоит посмотреть' }}
        </span>
        <span v-if="showProject" class="sig__ws">{{ s.workspace_name || 'Все проекты' }}</span>
        <button class="sig__hide" type="button" title="Скрыть до завтра" aria-label="Скрыть до завтра" @click="hide(s)">
          <X :size="15" aria-hidden="true" />
        </button>
      </div>
      <h3>{{ s.title }}</h3>
      <p v-if="s.note">{{ s.note }}</p>
      <div class="sig__acts">
        <RouterLink v-if="s.link" class="btn btn--accent" :to="s.link.to">{{ s.link.label }}</RouterLink>
        <button class="sig__ask" type="button" title="Спросить ИИ об этом" aria-label="Спросить ИИ об этом" @click="ask(s)">
          <Sparkles :size="16" aria-hidden="true" />
        </button>
      </div>
    </article>
  </div>
  <div v-else class="ccard ccard--note">
    {{
      hiddenCount
        ? 'Остальное вы скрыли до завтра — больше ничего не требует внимания.'
        : 'Сегодня всё спокойно: сервис не нашёл ничего, что требует внимания.'
    }}
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { Sparkles, TriangleAlert, X } from '@lucide/vue'
import { errorDetail } from '../api/web'
import { putHomeQuestion } from '../composables/useHomeAsk'
import { isoDay, problemLink } from '../utils/format'

/**
 * «Сервис заметил» простого режима: проблемы из сводки (без «к сведению») карточками.
 * Крестик прячет сигнал до завтра — в этом браузере, сервер о нём не знает.
 */
const props = defineProps({
  problems: { type: Array, default: () => [] },
  /** На общей главной — подпись проекта у каждой карточки. */
  showProject: Boolean,
  /** Проект страницы: «Спросить» уходит в его чат, без него — в общий. */
  wsId: { type: Number, default: null },
  limit: { type: Number, default: 8 },
  /** Без своей шапки «Сервис заметил» — когда заголовок уже есть на странице (мастер первого входа). */
  bare: Boolean,
})

const STORE = 'aimarketing-hidden-signals'
const router = useRouter()
const today = isoDay(new Date())

function readHidden() {
  try {
    const all = JSON.parse(localStorage.getItem(STORE) || '{}')
    // Скрытое вчера и раньше снова показываем и заодно чистим.
    return Object.fromEntries(Object.entries(all).filter(([, day]) => day === today))
  } catch {
    return {}
  }
}
const hidden = ref(readHidden())

const signals = computed(() =>
  props.problems
    .filter((p) => p.severity === 'critical' || p.severity === 'warning')
    .map((p) => ({
      ...p,
      key: [p.kind, p.workspace_id, p.account_id, p.campaign_row_id, p.title].join('|'),
      link: problemLink(p),
      note: [p.object_name, p.detail ? errorDetail(p.detail) : ''].filter(Boolean).join(' · '),
    }))
    .sort((a, b) => Number(b.severity === 'critical') - Number(a.severity === 'critical')),
)
const open = computed(() => signals.value.filter((s) => !hidden.value[s.key]))
const visible = computed(() => open.value.slice(0, props.limit))
const hiddenCount = computed(() => signals.value.length - open.value.length)

function hide(s) {
  hidden.value = { ...hidden.value, [s.key]: today }
  try {
    localStorage.setItem(STORE, JSON.stringify(hidden.value))
  } catch {
    // Без хранилища сигнал скроется только до перезагрузки.
  }
}

function ask(s) {
  const where = [s.workspace_name, s.object_name].filter(Boolean).join(', ')
  putHomeQuestion(
    `Сервис заметил: «${s.title}»${where ? ` (${where})` : ''}${s.note ? `. ${s.note}` : ''}. Почему так и что сделать?`,
  )
  const ws = props.wsId ?? s.workspace_id
  router.push({ path: ws ? `/w/${ws}/chat` : '/cabinet/chat', query: { ask: 'text' } })
}
</script>

<style scoped>
.section-h__note {
  margin-left: 6px;
  color: var(--faint);
  font-size: 12px;
  font-weight: 600;
}
.sigs {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
}
.sig {
  display: flex;
  flex-direction: column;
  padding: 16px 18px;
  border: 1px solid var(--line);
  border-radius: var(--r-card);
  background: var(--surface);
}
.sig__top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  color: var(--faint);
  font-size: 12px;
}
.sig__ws {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-align: right;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sig h3 {
  margin: 10px 0 0;
  font-size: 15px;
  font-weight: 700;
  line-height: 1.35;
}
.sig p {
  margin: 4px 0 0;
  color: var(--muted);
  font-size: 13px;
}
.sig__acts {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: auto;
  padding-top: 14px;
}
.sig__acts .btn {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* «Спросить ИИ» — квадратная кнопка-иконка справа, чтобы главное действие не делило с ней ширину. */
.sig__ask {
  display: grid;
  flex: none;
  place-items: center;
  width: 40px;
  height: 40px;
  margin-left: auto;
  padding: 0;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: var(--surface);
  color: var(--ink-2);
  cursor: pointer;
}
.sig__ask:hover {
  border-color: var(--ink);
  color: var(--ink);
}
.sig__ask:focus-visible {
  outline: 2px solid var(--ink);
  outline-offset: 2px;
}
.sig__hide {
  display: grid;
  flex-shrink: 0;
  place-items: center;
  width: 26px;
  height: 26px;
  margin: -4px -6px -4px auto;
  padding: 0;
  border: 0;
  border-radius: 8px;
  background: none;
  color: var(--faint);
  cursor: pointer;
}
.sig__ws + .sig__hide {
  margin-left: 0;
}
.sig__hide:hover {
  background: var(--line);
  color: var(--ink);
}
</style>
