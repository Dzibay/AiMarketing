<template>
  <div class="start">
    <div v-if="!state" class="ccard start__card">
      <p class="empty">{{ loadError || 'Загружаем…' }}</p>
      <button v-if="loadError" class="btn btn--ghost" type="button" @click="load">Повторить</button>
    </div>

    <template v-else>
      <ol class="stepper" aria-label="Шаги">
        <li v-for="(s, i) in steps" :key="s.id" :class="{ done: s.done, cur: s.id === step.id }">
          <button type="button" :aria-current="s.id === step.id ? 'step' : undefined" @click="selected = s.id">
            <i>
              <Check v-if="s.done" :size="13" aria-hidden="true" />
              <template v-else>{{ i + 1 }}</template>
            </i>
            <span>{{ LABELS[s.id] || s.title }}</span>
          </button>
        </li>
      </ol>

      <section class="ccard start__card">
        <!-- 1. Почта -->
        <template v-if="step.id === 'email'">
          <h2>{{ step.done ? 'Почта подтверждена' : 'Подтвердите почту' }}</h2>
          <p v-if="step.done" class="start__muted">
            {{ user?.email }} — на неё придут сводки и ссылка, если забудете пароль.
          </p>
          <p v-else class="start__muted">
            Мы отправили письмо со ссылкой на {{ user?.email }}. Откройте её — и можно продолжать.
          </p>
        </template>

        <!-- 2. Яндекс -->
        <template v-else-if="step.id === 'yandex'">
          <h2>Подключите Яндекс</h2>
          <p class="start__muted">
            Сервис читает вашу рекламу и статистику сайта. Вход — через страницу Яндекса, пароль мы не видим.
          </p>
          <div class="ccard start__list">
            <div class="prow">
              <Megaphone :size="18" class="start__ico" aria-hidden="true" />
              <div class="prow__main">
                <b>Яндекс Директ</b>
                <p>Кампании, объявления, фразы и расходы. Нужен, чтобы видеть картину и вносить изменения, которые вы подтвердили.</p>
              </div>
            </div>
            <div class="prow">
              <ChartColumn :size="18" class="start__ico" aria-hidden="true" />
              <div class="prow__main">
                <b>Яндекс Метрика</b>
                <p>Визиты и цели сайта. Нужна, чтобы считать заявки и их цену.</p>
              </div>
            </div>
          </div>
          <p class="start__good">
            <CircleCheck :size="16" aria-hidden="true" />
            <span>
              Сервис ничего не меняет в рекламе сам. Любое изменение ждёт вашей кнопки «Выполнить», а потом его можно
              вернуть в «Истории».
            </span>
          </p>
          <details v-if="!step.done" class="start__more">
            <summary>Счётчики и кабинеты на разных логинах?</summary>
            <p>Подключите их по очереди — на странице Яндекса можно выбрать любой логин. Агентский логин Директа подтянет все клиентские кабинеты сразу.</p>
            <div class="start__more-acts">
              <YandexConnectButton purpose="connect" mode="metrika" label="Только Метрика" variant="ghost" :wide="false" return-to="/cabinet/welcome" />
              <YandexConnectButton purpose="connect" mode="direct" label="Только Директ" variant="ghost" :wide="false" return-to="/cabinet/welcome" />
            </div>
          </details>
        </template>

        <!-- 3. Сайты -->
        <template v-else-if="step.id === 'projects'">
          <h2>Выберите сайты</h2>
          <p class="start__muted">
            <template v-if="siteRows.length">
              В вашем Яндексе {{ siteRows.length }} {{ plural(siteRows.length, 'счётчик', 'счётчика', 'счётчиков') }}
              Метрики. Один счётчик — один сайт в кабинете. Остальные можно добавить позже в «Подключениях».
            </template>
            <template v-else>Один счётчик Метрики — один сайт в кабинете.</template>
          </p>
          <div v-if="siteRows.length" class="ccard start__list">
            <div v-for="c in siteRows" :key="c.counter_id" class="prow" :class="{ 'is-off': c.state === 'taken' || c.state === 'locked' }">
              <div class="prow__main">
                <b>{{ c.site || c.name || `Счётчик ${c.counter_id}` }}</b>
                <p>
                  <template v-if="c.state === 'taken'">{{ c.note || 'Уже подключён в другом аккаунте' }}</template>
                  <template v-else-if="c.state === 'archived'">В архиве — вернётся со всей историей</template>
                  <template v-else>счётчик {{ c.counter_id }}<template v-if="c.name && c.site"> · {{ c.name }}</template></template>
                </p>
              </div>
              <div class="prow__acts">
                <span v-if="c.state === 'project'" class="st st--ok"><CircleCheck :size="14" aria-hidden="true" />В кабинете</span>
                <button
                  v-else-if="c.state === 'available' || c.state === 'archived'"
                  class="btn"
                  type="button"
                  :disabled="busy"
                  @click="addSite(c)"
                >
                  {{ adding === c.counter_id ? 'Добавляем…' : c.state === 'archived' ? 'Вернуть' : 'Добавить' }}
                </button>
                <RouterLink v-else-if="c.state === 'locked'" class="chip chip--lock" to="/cabinet/plan">
                  <Lock aria-hidden="true" /> на тарифе выше
                </RouterLink>
              </div>
            </div>
          </div>
          <p v-if="counters.projects_limit !== null && counters.projects_limit !== undefined && siteRows.length" class="start__note">
            Добавлено {{ counters.projects_used }} из {{ counters.projects_limit }} доступных на вашем тарифе.
          </p>
        </template>

        <!-- 4. Загрузка -->
        <template v-else-if="step.id === 'load'">
          <h2>{{ step.done ? 'Данные загружены' : 'Загружаем данные' }}</h2>
          <p class="start__muted">
            Обычно это занимает пару минут. Дальше сервис обновляет данные сам, несколько раз в день. Страницу можно
            закрыть.
          </p>
          <ul class="start__check">
            <li v-for="t in loadList" :key="t.text" :class="{ 'is-wait': !t.done }">
              <CircleCheck v-if="t.done" :size="15" aria-hidden="true" />
              <span v-else class="spinner" aria-hidden="true"></span>
              <span>{{ t.text }}</span>
            </li>
          </ul>
        </template>

        <!-- 5. Готово -->
        <template v-else-if="step.id === 'ai'">
          <h2>Готово. Вот что сервис уже заметил</h2>
          <p class="start__muted">
            Это посчитал код, без ИИ — лимит не потрачен. Чтобы понять, что делать дальше, спросите ИИ: он видит все
            ваши сайты, а правки в Директе вносит только после вашего «Выполнить».
          </p>
          <p v-if="!overview" class="empty">Собираем сигналы…</p>
          <SignalCards v-else :problems="overview.problems || []" show-project bare :limit="3" />
        </template>

        <p v-if="!step.done && step.detail" class="start__detail" :class="DETAIL_TONES[step.status]">
          <TriangleAlert v-if="step.status === 'error' || step.status === 'blocked'" :size="14" aria-hidden="true" />
          {{ step.detail }}
        </p>

        <div class="start__foot">
          <template v-if="step.id === 'email' && !step.done">
            <RouterLink class="btn btn--accent" to="/verify-email">Отправить письмо ещё раз</RouterLink>
          </template>
          <template v-else-if="step.id === 'yandex' && !step.done">
            <YandexConnectButton purpose="connect" label="Войти через Яндекс" :wide="false" return-to="/cabinet/welcome" />
          </template>
          <template v-else-if="step.id === 'ai'">
            <RouterLink class="btn btn--accent" to="/cabinet">Открыть кабинет<ArrowRight :size="15" aria-hidden="true" /></RouterLink>
            <RouterLink class="btn" :to="{ path: '/cabinet/chat', query: { ask: 'first' } }">
              <Sparkles :size="15" aria-hidden="true" />Спросить ИИ: с чего начать
            </RouterLink>
          </template>
          <button
            v-if="nextStep"
            class="btn"
            :class="{ 'btn--accent': step.done || step.id === 'load' || step.id === 'projects' }"
            type="button"
            :disabled="step.id === 'projects' && !step.done"
            @click="selected = nextStep.id"
          >
            {{ step.id === 'projects' ? 'Загрузить данные' : step.id === 'load' ? 'Посмотреть, что получилось' : 'Дальше' }}
            <ArrowRight :size="15" aria-hidden="true" />
          </button>
          <button v-if="prevStep" class="btn btn--ghost" type="button" @click="selected = prevStep.id">Назад</button>
        </div>
      </section>

      <p class="start__meta">
        Шаги отмечаются сами по данным кабинета.
        <template v-if="!wizard.complete">
          Можно
          <button class="start__skip" type="button" :disabled="busy" @click="skip">пропустить мастер</button>
          и вернуться к нему с Главной.
        </template>
      </p>

      <section v-if="wizard.complete && extras.length" class="start__extras">
        <h2 class="section-h">Что ещё настроить <small class="section-h__note">необязательно, но с этим помощник точнее</small></h2>
        <div class="ccard">
          <div v-for="s in extras" :key="s.id" class="prow">
            <div class="prow__main">
              <b>{{ s.title }}</b>
              <p>{{ s.hint }}</p>
            </div>
            <div class="prow__acts">
              <RouterLink v-if="s.to && s.status !== 'blocked'" class="btn btn--ghost" :to="s.to">Открыть</RouterLink>
            </div>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import {
  ArrowRight,
  ChartColumn,
  Check,
  CircleCheck,
  Lock,
  Megaphone,
  Sparkles,
  TriangleAlert,
} from '@lucide/vue'
import SignalCards from '../components/SignalCards.vue'
import YandexConnectButton from '../components/YandexConnectButton.vue'
import { errorText, webApi, wsApi } from '../api/web'
import { loadProfile, useProfile } from '../composables/useProfile'
import { pushToast } from '../composables/useToasts'
import { loadWorkspaces, useWorkspaces } from '../composables/useWorkspaces'
import { plural } from '../utils/format'
import { useHead } from '../utils/useHead'

useHead('Начало работы — AiMarketing', true)

const POLL_MS = 10000
// Подписи степпера — короче заголовков шагов с сервера.
const LABELS = { email: 'Почта', yandex: 'Яндекс', projects: 'Сайты', load: 'Загрузка', ai: 'Вопрос ИИ' }
const DETAIL_TONES = { error: 'is-bad', progress: 'is-wait', blocked: 'is-warn' }
// Свободные — сверху, затем уже добавленные, архив, тариф выше и занятые в других аккаунтах.
const ORDER = { available: 0, project: 1, archived: 2, locked: 3, taken: 4 }

const router = useRouter()
const { user } = useProfile()
const { workspaces } = useWorkspaces()
const state = ref(null)
const loadError = ref('')
const busy = ref(false)
const adding = ref(null)
const selected = ref('')
const counters = ref({ items: [], projects_limit: null, projects_used: 0 })
const overview = ref(null)
let timer = 0

const wizard = computed(() => state.value?.wizard || { steps: [], current: null, complete: false })
const steps = computed(() => wizard.value.steps)
const step = computed(() => steps.value.find((s) => s.id === selected.value) || steps.value[0] || {})
const index = computed(() => steps.value.findIndex((s) => s.id === step.value.id))
const prevStep = computed(() => (index.value > 0 ? steps.value[index.value - 1] : null))
const nextStep = computed(() => steps.value[index.value + 1] || null)
// После мастера — необязательные шаги прежнего чек-листа, которые ещё не сделаны.
const extras = computed(() =>
  (state.value?.steps || []).filter((s) => !s.done && ['campaigns', 'link_template', 'goals', 'telegram'].includes(s.id)),
)
const siteRows = computed(() =>
  counters.value.items.filter((c) => c.state in ORDER).sort((a, b) => ORDER[a.state] - ORDER[b.state]),
)

const loadList = computed(() => {
  const done = !!step.value.done
  const campaigns = workspaces.value.filter((w) => !w.archived).reduce((n, w) => n + (w.campaigns_count || 0), 0)
  return [
    {
      done,
      text:
        done && campaigns
          ? `Кампании, группы и объявления из Директа: ${campaigns} ${plural(campaigns, 'кампания', 'кампании', 'кампаний')}`
          : 'Кампании, группы и объявления из Директа',
    },
    { done, text: 'Расходы и клики за последние 90 дней' },
    { done, text: 'Цели и заявки из Метрики' },
    { done, text: 'Поисковые запросы — догружаются в течение суток' },
  ]
})

function schedule() {
  window.clearTimeout(timer)
  if (steps.value.some((s) => s.status === 'progress')) timer = window.setTimeout(load, POLL_MS)
}

async function load() {
  loadError.value = ''
  const before = wizard.value.current
  try {
    state.value = await webApi.onboarding()
  } catch (err) {
    loadError.value = errorText(err)
    return
  }
  const current = wizard.value.current
  // Шаг сделан — переводим на следующий, если пользователь стоял на нём.
  if (!selected.value || (before && before !== current && selected.value === before)) {
    selected.value = current || 'ai'
  }
  if (before !== current) loadWorkspaces(true).catch(() => null)
  schedule()
}

async function loadCounters() {
  try {
    counters.value = await wsApi.counters()
  } catch {
    // Не критично: список подтянется при следующем открытии шага.
  }
}

async function loadOverview() {
  try {
    overview.value = await webApi.overview()
  } catch {
    overview.value = { problems: [] }
  }
}

async function addSite(c) {
  busy.value = true
  adding.value = c.counter_id
  try {
    const ws = await wsApi.fromCounter(c.counter_id)
    pushToast(`${ws.name || c.site || c.counter_id} добавлен`)
    await Promise.all([loadCounters(), loadWorkspaces(true).catch(() => null), load()])
  } catch (err) {
    pushToast(errorText(err), 'error')
    await loadCounters()
  } finally {
    busy.value = false
    adding.value = null
  }
}

async function skip() {
  busy.value = true
  try {
    await webApi.dismissOnboarding()
    router.push('/cabinet')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

// Вернулись на вкладку (например, после входа в Яндекс в соседней) — перечитаем шаги.
function onVisible() {
  if (document.visibilityState === 'visible') load()
}

watch(
  () => step.value.id,
  (id) => {
    if (id === 'projects') {
      loadCounters()
      loadWorkspaces(true).catch(() => null)
    }
    if (id === 'ai' && !overview.value) loadOverview()
  },
)

onMounted(() => {
  loadProfile().catch(() => null)
  loadWorkspaces().catch(() => null)
  load()
  document.addEventListener('visibilitychange', onVisible)
})
onBeforeUnmount(() => {
  window.clearTimeout(timer)
  document.removeEventListener('visibilitychange', onVisible)
})
</script>

<style scoped>
.start {
  max-width: 660px;
  margin: 0 auto;
  padding: 8px 0 40px;
}
.stepper {
  display: flex;
  gap: 6px;
  margin: 0 0 16px;
  padding: 0;
  list-style: none;
}
.stepper li {
  flex: 1;
  min-width: 0;
}
.stepper button {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 0;
  border: 0;
  border-top: 3px solid var(--line);
  background: none;
  color: var(--faint);
  font: inherit;
  font-size: 13px;
  font-weight: 700;
  text-align: left;
  cursor: pointer;
}
.stepper button span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.stepper i {
  display: grid;
  flex: none;
  place-items: center;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--line);
  color: var(--muted);
  font-size: 11px;
  font-style: normal;
}
.stepper li.done button {
  border-top-color: var(--ink);
  color: var(--muted);
}
.stepper li.done i {
  background: var(--ink);
  color: var(--surface);
}
.stepper li.cur button {
  border-top-color: var(--lime);
  color: var(--ink);
}
.stepper li.cur i {
  background: var(--lime);
  color: #141413;
}
.start__card {
  padding: 24px 26px;
}
.start__card > h2 {
  margin: 0 0 6px;
  font-size: 22px;
  letter-spacing: -0.02em;
}
.start__muted {
  margin: 0 0 16px;
  color: var(--muted);
  line-height: 1.5;
}
.start__list {
  margin-bottom: 4px;
}
.start__list .prow {
  align-items: flex-start;
}
.start__ico {
  flex: none;
  margin-top: 2px;
  color: var(--muted);
}
.prow.is-off .prow__main {
  opacity: 0.6;
}
.start__good {
  display: flex;
  gap: 8px;
  margin: 14px 0 0;
  font-size: 14px;
  line-height: 1.5;
}
.start__good svg {
  flex: none;
  margin-top: 2px;
  color: var(--ok);
}
.start__more {
  margin-top: 14px;
  font-size: 14px;
}
.start__more summary {
  color: var(--muted);
  cursor: pointer;
}
.start__more p {
  margin: 8px 0;
  color: var(--muted);
  line-height: 1.5;
}
.start__more-acts {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.start__note {
  margin: 12px 0 0;
  color: var(--muted);
  font-size: 14px;
}
.start__check {
  display: grid;
  gap: 10px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.start__check li {
  display: flex;
  align-items: center;
  gap: 10px;
}
.start__check svg {
  flex: none;
  color: var(--ok);
}
.start__check li.is-wait {
  color: var(--muted);
}
.start__detail {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 14px 0 0;
  color: var(--muted);
  font-size: 14px;
}
.start__detail.is-bad {
  color: var(--danger);
}
.start__detail.is-warn {
  color: var(--c-amber, var(--muted));
}
.start__foot {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-top: 22px;
}
.start__foot .btn {
  gap: 6px;
}
.start__card :deep(.section-h) {
  margin-top: 4px;
}
.start__meta {
  margin: 14px 2px 0;
  color: var(--faint);
  font-size: 13px;
}
.start__skip {
  padding: 0;
  border: 0;
  background: none;
  color: var(--muted);
  font: inherit;
  text-decoration: underline;
  cursor: pointer;
}
.start__extras {
  margin-top: 28px;
}
@media (max-width: 640px) {
  .start__card {
    padding: 18px 16px;
  }
  .stepper span {
    display: none;
  }
  .stepper li.cur span {
    display: inline;
  }
}
</style>
