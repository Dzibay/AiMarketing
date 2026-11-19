<template>
  <h2 v-if="!bare" class="section-h">
    <span class="hico" aria-hidden="true"><Radar /></span>Сервис заметил <span class="cgroup__n">{{ visible.length }}</span>
    <small class="section-h__note">по цифрам, без ИИ — лимит не тратится</small>
    <!-- На общей главной — разбор всех проектов разом в общем чате. -->
    <small v-if="showProject && !wsId && visible.length > 1" class="home-link">
      <RouterLink :to="{ path: '/cabinet/chat', query: { ask: 'problems_all' } }">Разобрать всё с ИИ</RouterLink>
    </small>
  </h2>
  <!-- Утренний разбор на общей главной: «Со вчера: 3 новых, 2 решились». -->
  <p v-if="review" class="sig-review">{{ review }}</p>
  <div v-if="visible.length" class="sigs">
    <article v-for="s in visible" :key="s.uid" class="sig">
      <div class="sig__top">
        <span class="st" :class="s.severity === 'critical' ? 'st--bad' : 'st--warn'">
          <TriangleAlert :size="14" aria-hidden="true" />
          {{ s.severity === 'critical' ? 'Срочно' : 'Стоит посмотреть' }}
        </span>
        <b v-if="s.is_new" class="sig__new">новое</b>
        <span v-if="showProject" class="sig__ws">{{ niceQuotes(s.workspace_name) || 'Все проекты' }}</span>
        <span class="sig__hide"><SnoozeMenu :problem="s" @snoozed="onSnoozed" /></span>
      </div>
      <h3>{{ s.title }}</h3>
      <p v-if="s.note">{{ s.note }}</p>
      <p v-if="s.kind === 'balance' && onRefreshBalance" class="sig__more">
        Уже пополнили?
        <button class="link-btn" type="button" :disabled="balanceRefreshing" @click="onRefreshBalance()">
          {{ balanceRefreshing ? 'Обновляем баланс…' : 'Обновить баланс' }}
        </button>
      </p>
      <div class="sig__acts">
        <a v-if="s.link?.external" class="btn btn--accent" :href="s.link.to" target="_blank" rel="noopener">
          <span class="sig__label">{{ s.link.label }}</span>
        </a>
        <RouterLink v-else-if="s.link" class="btn btn--accent" :to="s.link.to"><span class="sig__label">{{ s.link.label }}</span></RouterLink>
        <button class="sig__ask" type="button" v-tip="'Спросить ИИ об этом'" aria-label="Спросить ИИ об этом" @click="ask(s)">
          <Sparkles :size="16" aria-hidden="true" />
        </button>
      </div>
    </article>
  </div>
  <EmptyState
    v-else
    art="calm"
    :plain="bare"
    :text="
      hiddenCount
        ? 'Остальное вы скрыли — больше ничего не требует внимания. Скрытое — в строке ниже.'
        : 'Сегодня всё спокойно: сервис не нашёл ничего, что требует внимания. Можно поискать, где вырасти.'
    "
  >
    <template v-if="!hiddenCount && !bare" #action>
      <RouterLink class="btn btn--ghost" :to="wsId ? `/w/${wsId}/chat` : '/cabinet/chat'">
        <Sparkles :size="15" aria-hidden="true" />Спросить ИИ, что улучшить
      </RouterLink>
    </template>
  </EmptyState>
  <SnoozedList :items="hiddenItems" @changed="emit('changed')" />
</template>

<script setup>
import { computed } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { Radar, Sparkles, TriangleAlert } from '@lucide/vue'
import EmptyState from './ui/EmptyState.vue'
import SnoozeMenu from './SnoozeMenu.vue'
import SnoozedList from './SnoozedList.vue'
import { errorDetail } from '../api/web'
import { putHomeQuestion } from '../composables/useHomeAsk'
import { useSnoozed } from '../composables/useSnoozed'
import { niceQuotes, problemLink } from '../utils/format'

/**
 * «Сервис заметил» простого режима: проблемы из сводки (без «к сведению») карточками.
 * «⋯» скрывает сигнал на сервере (на 7 дней, «так задумано» или весь тип) — он уходит из счётчиков,
 * дайджеста и уведомлений; внизу свёрнутая строка «Скрыто: N» с «Показать снова». @changed — перечитать сводку.
 */
const props = defineProps({
  problems: { type: Array, default: () => [] },
  /** Строки hidden из сводки: что скрыто через «⋯». */
  hidden: { type: Array, default: () => [] },
  /** На общей главной — подпись проекта у каждой карточки. */
  showProject: Boolean,
  /** Проект страницы: «Спросить» уходит в его чат, без него — в общий. */
  wsId: { type: Number, default: null },
  limit: { type: Number, default: 8 },
  /** Без своей шапки «Сервис заметил» — когда заголовок уже есть на странице (мастер первого входа). */
  bare: Boolean,
  /** @refresh-balance — у карточки о деньгах «Уже пополнили? Обновить баланс»; без обработчика ссылки нет. */
  onRefreshBalance: { type: Function, default: null },
  balanceRefreshing: Boolean,
  /** Строка утреннего разбора («Со вчера: …»); метки «новое» — по is_new проблем. */
  review: { type: String, default: '' },
})

const emit = defineEmits(['changed'])
const router = useRouter()
// Скрытое раньше в этом браузере («до завтра», localStorage) больше не читаем — скрытия живут на сервере.
const { mark, isGone } = useSnoozed(() => props.problems)

const signals = computed(() =>
  props.problems
    .filter((p) => (p.severity === 'critical' || p.severity === 'warning') && !isGone(p))
    .map((p) => ({
      ...p,
      uid: [p.key || p.kind, p.workspace_id, p.account_id, p.campaign_row_id, p.title].join('|'),
      link: problemLink(p),
      note: [p.object_name, p.detail ? errorDetail(p.detail) : ''].filter(Boolean).join(' · '),
    }))
    .sort((a, b) => Number(b.severity === 'critical') - Number(a.severity === 'critical')),
)
const visible = computed(() => signals.value.slice(0, props.limit))
// В «Скрыто» — то, что было бы карточкой: срочное и «стоит посмотреть», плюс скрытые типы целиком.
const hiddenItems = computed(() =>
  props.hidden.filter((h) => h.mode === 'type' || h.severity === 'critical' || h.severity === 'warning'),
)
const hiddenCount = computed(() => hiddenItems.value.length)

function onSnoozed(g) {
  mark(g)
  emit('changed')
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
/* Не уже 280 px и не больше четырёх в ряд (на 1600 px — четыре); все строки одной высоты, кнопки — внизу карточки. */
.sigs {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(max(280px, calc((100% - 36px) / 4)), 1fr));
  grid-auto-rows: 1fr;
  gap: 12px;
}
/* В одну колонку выравнивать высоту незачем — короткие карточки не растягиваем. */
@media (max-width: 640px) {
  .sigs {
    grid-auto-rows: auto;
  }
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
.sig-review {
  margin: -4px 0 10px;
  color: var(--ink);
  font-weight: 600;
}
.sig__new {
  flex: none;
  padding: 1px 7px;
  border-radius: 999px;
  background: var(--lime);
  color: #141413;
  font-size: 11px;
  font-weight: 700;
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
.sig p.sig__more {
  color: var(--faint);
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
}
/* Многоточие — у подписи, а не у кнопки: кнопка с overflow: hidden обрезала бы свою область нажатия. */
.sig__label {
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
/* «⋯» в углу карточки — тихая кнопка без рамки, чтобы не спорить с главным действием. */
.sig__hide {
  display: flex;
  flex-shrink: 0;
  margin: -6px -8px -6px auto;
}
.sig__ws + .sig__hide {
  margin-left: 0;
}
.sig__hide :deep(.ui-menu__btn:not(.is-open):not(:hover)) {
  border-color: transparent;
  background: none;
  color: var(--faint);
}
</style>
