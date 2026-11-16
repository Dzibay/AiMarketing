<template>
  <div class="ws-toolbar">
    <p class="sync-status">
      Предложения ИИ {{ project ? `по сайту «${niceQuotes(project.name)}»` : 'по всем сайтам' }}, которые ждут вашего решения. В
      Директе ничего не меняется, пока вы не нажмёте «Выполнить».
    </p>
  </div>

  <UiSkeleton v-if="loading && !items.length && !snoozed.length" variant="rows" :count="3" actions label="Загружаем предложения" />
  <div v-else-if="loadError" class="ccard ccard--note tone-bad">{{ loadError }}</div>
  <template v-else>
    <EmptyState
      v-if="!items.length"
      art="done"
      :text="
        snoozed.length
          ? 'Сейчас решений ничего не ждёт — остались только отложенные, они ниже.'
          : 'Решений ничего не ждёт. Новые предложения ИИ появятся здесь, на главной и в чатах.'
      "
    >
      <template #action><RouterLink class="btn btn--accent" :to="chatHome">Спросить ИИ</RouterLink></template>
    </EmptyState>
    <section v-for="g in groups" :key="g.key">
      <h2 class="section-h pend-h">
        <template v-if="g.snoozed">
          <span>Отложено</span>
          <span class="cgroup__n">{{ snoozedTotal }}</span>
          <button class="btn btn--ghost btn--sm pend-h__toggle" type="button" :aria-expanded="showSnoozed" @click="showSnoozed = !showSnoozed">
            {{ showSnoozed ? 'Свернуть' : 'Показать' }}
          </button>
        </template>
        <template v-else>
          <RouterLink v-if="g.project" :to="`/w/${g.project.id}/summary`">{{ niceQuotes(g.project.name) }}</RouterLink>
          <span v-else>Все сайты</span>
          <span class="cgroup__n">{{ g.items.filter((a) => !doneIds[a.id]).length }}</span>
        </template>
      </h2>
      <p v-if="g.snoozed && !showSnoozed" class="meta-note">
        Вы отложили их кнопкой «Позже». В счётчик «Ждёт решения» они не входят, но ничего не выполнено.
      </p>
      <div v-if="!g.snoozed || showSnoozed" class="ccard pend-list">
        <template v-for="a in g.items" :key="a.id">
        <!-- Только что выполнено: строка с галочкой на несколько секунд, потом уходит из списка. -->
        <article v-if="doneIds[a.id]" class="prow pend pend--done is-fresh" :data-action="a.id" role="status">
          <span class="done-check"><Check aria-hidden="true" /></span>
          <div class="prow__main">
            <b>{{ a.label }}</b>
            <p>
              {{ doneIds[a.id] }}
              <RouterLink :to="chatLink(a)">«{{ a.chat.title }}»</RouterLink>
            </p>
          </div>
        </article>
        <article v-else class="prow pend" :data-action="a.id">
          <div class="prow__main">
            <small>
              <template v-if="g.snoozed">{{ a.project ? niceQuotes(a.project.name) : 'Все сайты' }} · </template>
              {{ formatDateTime(a.created_at) }} · {{ a.chat.general ? 'общий чат' : 'чат' }} «{{ a.chat.title }}»
            </small>
            <b>{{ a.label }}</b>
            <p v-if="a.summary">{{ a.summary }}</p>
            <p v-else-if="a.kind === 'ask'">{{ ASK_TEXT[a.tool] || ASK_TEXT.ask_questions }}</p>
            <AiChanges v-if="a.changes?.length" class="pend__changes" :changes="a.changes" />
            <p v-if="a.reason" class="pend__why">Зачем: {{ a.reason }}</p>
            <p v-if="a.categories?.length" class="pend__cats">
              <span v-for="c in a.categories" :key="c.id" class="chip">{{ c.label }}</span>
            </p>
            <p v-if="a.kind === 'write' && a.items?.length" class="pend__why">
              Отдельные строки снимаются только в чате — здесь действие выполнится целиком.
            </p>
          </div>
          <div class="prow__acts prow__acts--decide">
            <template v-if="a.kind === 'write'">
              <button
                v-coach="'proposal'"
                class="btn btn--accent"
                type="button"
                :disabled="!!busy[a.id]"
                @click="decide(a, true)"
              >
                Выполнить
              </button>
              <button class="btn btn--ghost" type="button" :disabled="!!busy[a.id]" @click="decide(a, false)">
                Отклонить
              </button>
            </template>
            <RouterLink v-if="a.kind !== 'write'" class="btn btn--accent" :to="chatLink(a)">Ответить в чате</RouterLink>
            <template v-else>
              <RouterLink v-if="!phone" class="btn btn--ghost" :to="chatLink(a)">Открыть чат</RouterLink>
              <!-- «Позже» — в «⋯»; на телефоне туда же чат: кнопки в ряд не помещаются. -->
              <UiMenu :disabled="!!busy[a.id]">
                <UiMenuItem
                  :icon="a.snoozed ? Undo2 : Clock"
                  :hint="a.snoozed ? 'снова в счётчике «Ждёт решения»' : 'убрать из счётчика, решить потом'"
                  @click="snooze(a, !a.snoozed)"
                >
                  {{ a.snoozed ? 'Вернуть в ждущие' : 'Позже' }}
                </UiMenuItem>
                <UiMenuItem v-if="phone" :icon="MessageSquare" :to="chatLink(a)">Открыть чат</UiMenuItem>
              </UiMenu>
            </template>
          </div>
        </article>
        </template>
      </div>
    </section>
    <p v-if="total > items.length" class="meta-note">Показаны первые {{ items.length }} из {{ total }}.</p>
  </template>
</template>

<script setup>
import UiSkeleton from '../components/ui/UiSkeleton.vue'
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { Check, Clock, MessageSquare, Undo2 } from '@lucide/vue'
import EmptyState from '../components/ui/EmptyState.vue'
import UiMenu from '../components/ui/UiMenu.vue'
import UiMenuItem from '../components/ui/UiMenuItem.vue'
import { aiApi, errorText } from '../api/web'
import { refreshPending, usePending } from '../composables/usePending'
import { useProjectFilter } from '../composables/useProjectFilter'
import { pushToast } from '../composables/useToasts'
import { useViewport } from '../composables/useViewport'
import { foldFrom, heightOf } from '../utils/foldAnim'
import { formatDateTime, niceQuotes } from '../utils/format'
import { useHead } from '../utils/useHead'
import AiChanges from './workspace/AiChanges.vue'

useHead('Ждёт решения — Толк', true)

// Карточки вопросов, плана и таблицы решаются только в чате: там весь контекст ответа.
const ASK_TEXT = {
  ask_questions: 'ИИ задал вопросы — ответьте в чате, чтобы он продолжил.',
  propose_plan: 'ИИ предложил план — согласуйте шаги в чате.',
  propose_items: 'ИИ предложил таблицу — отметьте нужные строки в чате.',
}

const { projectId, project } = useProjectFilter()
const { phone } = useViewport()

const items = ref([])
const total = ref(0)
// Отложенные «Позже»: отдельной группой в конце, свёрнуты — ждут, но не торопят.
const snoozed = ref([])
const snoozedTotal = ref(0)
const showSnoozed = ref(false)
const loading = ref(false)
const loadError = ref('')
const busy = reactive({})
// Выполненные здесь: id → подпись строки с галочкой. Строка держится DONE_MS, потом карточка уходит.
const doneIds = reactive({})
const DONE_MS = 4000
// Решённые здесь: запрос списка, ушедший до решения, не должен вернуть карточку обратно.
const decided = new Set()

const chatHome = computed(() => (projectId.value ? `/w/${projectId.value}/chat` : '/cabinet/chat'))

/** По проектам — в порядке первой карточки; общие вопросы без проекта — после них; отложенные — в самом конце. */
const groups = computed(() => {
  const map = new Map()
  for (const a of items.value) {
    const key = a.project ? `p${a.project.id}` : 'none'
    if (!map.has(key)) map.set(key, { key, project: a.project, items: [] })
    map.get(key).items.push(a)
  }
  const out = [...map.values()].sort((x, y) => Number(!x.project) - Number(!y.project))
  if (snoozed.value.length) out.push({ key: 'snoozed', snoozed: true, project: null, items: snoozed.value })
  return out
})

function chatLink(a) {
  const base = a.chat.general || !a.project ? '/cabinet/chat' : `/w/${a.project.id}/chat`
  return `${base}/${a.chat.id}`
}

let seq = 0
async function load() {
  const n = ++seq
  loading.value = true
  loadError.value = ''
  try {
    const res = await aiApi.pending(projectId.value)
    if (n !== seq) return
    const open = (res.items || []).filter((a) => !decided.has(a.id))
    const later = (res.snoozed || []).filter((a) => !decided.has(a.id))
    total.value = Math.max(open.length, (res.total || 0) - (res.items || []).length + open.length)
    snoozedTotal.value = Math.max(later.length, (res.snoozed_total || 0) - (res.snoozed || []).length + later.length)
    items.value = keepDone(open, items.value)
    snoozed.value = keepDone(later, snoozed.value)
  } catch (err) {
    if (n === seq) loadError.value = errorText(err)
  } finally {
    if (n === seq) loading.value = false
  }
  refreshPending()
}

/** Свежий список с сервера плюс строки «выполнено», которые ещё не досветились: сервер их уже не отдаёт. */
function keepDone(list, before) {
  const done = before.filter((a) => doneIds[a.id] && !list.some((x) => x.id === a.id))
  return done.length ? [...list, ...done].sort((x, y) => y.id - x.id) : list
}

/** Убрать карточку из её списка: из ждущих или из отложенных. count=false — счётчик уже уменьшен. */
function drop(a, count = true) {
  if (a.snoozed) {
    snoozed.value = snoozed.value.filter((x) => x.id !== a.id)
    if (count) snoozedTotal.value = Math.max(0, snoozedTotal.value - 1)
  } else {
    items.value = items.value.filter((x) => x.id !== a.id)
    if (count) total.value = Math.max(0, total.value - 1)
  }
}

/** «Позже» (on) или «Вернуть в ждущие»: карточка переходит в другую группу без перезагрузки списка. */
async function snooze(a, on) {
  busy[a.id] = true
  try {
    const { action } = await aiApi.snooze(a.id, on)
    drop(a)
    const moved = { ...a, snoozed: action.snoozed }
    if (on) {
      snoozed.value = [moved, ...snoozed.value]
      snoozedTotal.value += 1
    } else {
      items.value = [moved, ...items.value].sort((x, y) => y.id - x.id)
      total.value += 1
    }
    pushToast(on ? 'Отложено — в конце списка, в счётчик не входит' : 'Снова ждёт решения')
  } catch (err) {
    pushToast(errorText(err), 'error')
    load()
  } finally {
    delete busy[a.id]
    refreshPending()
  }
}

async function decide(a, approve) {
  busy[a.id] = true
  try {
    const res = await aiApi.decide(a.id, approve, false, [])
    decided.add(a.id)
    if (!approve) {
      drop(a)
      pushToast('Отклонено')
      return
    }
    // «Выполнить» — карточка сворачивается в строку с галочкой на месте, а не пропадает молча.
    const selector = `.pend[data-action="${a.id}"]`
    const from = heightOf(selector)
    doneIds[a.id] = res.pending
      ? `Одобрено. ИИ продолжит, когда решите остальное (${res.pending}) в чате`
      : 'Выполняем — ход ИИ продолжится в чате'
    if (a.snoozed) snoozedTotal.value = Math.max(0, snoozedTotal.value - 1)
    else total.value = Math.max(0, total.value - 1)
    nextTick(() => foldFrom(selector, from))
    setTimeout(() => {
      delete doneIds[a.id]
      drop(a, false)
    }, DONE_MS)
  } catch (err) {
    pushToast(errorText(err), 'error')
    load()
  } finally {
    delete busy[a.id]
    refreshPending()
  }
}

watch(projectId, load, { immediate: true })

// Кнопки «Обновить» нет: шапка раз в минуту и при возврате на вкладку пересчитывает «Ждёт решения» —
// изменилось число (новое предложение или решение в чате) — подтягиваем список.
const { pendingCount } = usePending()
watch(pendingCount, () => {
  if (!loading.value) load()
})
</script>

<style scoped>
.pend-h {
  display: flex;
  align-items: center;
  gap: 8px;
}
.pend-h a {
  color: inherit;
  text-decoration: none;
}
.pend-h a:hover {
  text-decoration: underline;
  text-underline-offset: 3px;
}
.pend-h__toggle {
  margin-left: auto;
}
.pend {
  align-items: flex-start;
}
.pend .prow__main {
  flex: 1;
}
.pend.pend--done {
  align-items: center;
  justify-content: flex-start;
  gap: 12px;
  animation: pend-done-flash 1.2s var(--ease-out);
}
.pend--done .prow__main p {
  margin: 2px 0 0;
  color: var(--muted);
  font-size: 13px;
}
@keyframes pend-done-flash {
  from {
    background: var(--lime-soft);
  }
}
/* Таблице «Было → станет» нужно 360 px — иначе кнопки переносятся под текст (shell.css, .prow). */
@media (min-width: 721px) {
  .pend .prow__main {
    flex-basis: 360px;
  }
}
/* AiChanges — без общего корня (таблица и «Показать все»), поэтому через :deep. */
.prow__main :deep(.pend__changes) {
  margin-top: 8px;
}
.prow__main .pend__why {
  font-size: 13px;
}
.prow__main .pend__cats {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}
@media (max-width: 720px) {
  .pend .prow__acts {
    flex-wrap: wrap;
    gap: 8px;
  }
}
/* Телефон: кнопки карточки держатся у низа экрана, пока карточка видна, — до «Выполнить» не нужно листать. */
@media (max-width: 640px) {
  /* clip, а не hidden: скругление остаётся, а кнопки прилипают к экрану, а не к списку. */
  .pend-list {
    overflow: clip;
  }
  .pend {
    gap: 12px;
    padding-bottom: 0;
  }
  .pend .prow__acts {
    position: sticky;
    bottom: var(--bbar-h);
    z-index: 2;
    flex-wrap: nowrap;
    margin: 0 -18px;
    padding: 10px 18px 12px;
    border-top: 1px solid var(--line);
    background: var(--surface);
  }
}
</style>
