<template>
  <p class="page-lead">
    Пять шагов — и ИИ-помощник начнёт работать с вашими сайтами. Шаги отмечаются сами по данным кабинета, мастер
    можно закрыть и вернуться к нему из Сводки.
  </p>

  <div v-if="!state" class="panel">
    <p class="empty">{{ loadError || 'Загружаем…' }}</p>
    <button v-if="loadError" class="btn btn--ghost" type="button" @click="load">Повторить</button>
  </div>

  <template v-else>
    <ol class="wizard-steps" aria-label="Шаги">
      <li v-for="(s, i) in steps" :key="s.id" :class="{ done: s.done, active: s.id === selected }">
        <button type="button" :aria-current="s.id === selected ? 'step' : undefined" @click="selected = s.id">
          <Check v-if="s.done" class="inline-icon" aria-hidden="true" />
          <template v-else>{{ i + 1 }}.</template>
          {{ s.title }}
        </button>
      </li>
    </ol>

    <section class="panel welcome-step">
      <div class="panel__head">
        <div>
          <h2>{{ step.title }}</h2>
          <p class="hint">{{ step.hint }}</p>
        </div>
        <span class="chip" :class="STATUS_CHIPS[step.status]?.cls">{{ STATUS_CHIPS[step.status]?.text }}</span>
      </div>
      <p v-if="!step.done && step.detail" class="sync-status" :class="DETAIL_TONES[step.status]">{{ step.detail }}</p>

      <!-- 1. Почта -->
      <template v-if="step.id === 'email'">
        <p v-if="step.done">Почта {{ user?.email }} подтверждена — на неё придёт ссылка, если забудете пароль.</p>
        <p v-else>
          Откройте ссылку из письма на {{ user?.email }} —
          <RouterLink to="/verify-email">отправить письмо ещё раз</RouterLink>.
        </p>
      </template>

      <!-- 2. Яндекс -->
      <template v-else-if="step.id === 'yandex'">
        <p v-if="step.done">Метрика и Директ подключены. Ещё логины можно добавить в «Подключениях».</p>
        <div v-else class="welcome-connect">
          <YandexConnectButton purpose="connect" label="Подключить Метрику и Директ" :wide="false" return-to="/cabinet/welcome" />
          <p class="hint">
            Счётчики и кабинеты на разных логинах?
            <YandexConnectButton purpose="connect" mode="metrika" label="Только Метрика" variant="ghost" :wide="false" return-to="/cabinet/welcome" />
            <YandexConnectButton purpose="connect" mode="direct" label="Только Директ" variant="ghost" :wide="false" return-to="/cabinet/welcome" />
          </p>
          <p class="hint">
            На странице Яндекса можно выбрать любой логин. Агентский логин Директа подтянет все клиентские кабинеты сразу.
          </p>
        </div>
      </template>

      <!-- 3. Проекты -->
      <template v-else-if="step.id === 'projects'">
        <div v-if="activeProjects.length" class="list">
          <div v-for="w in activeProjects" :key="w.id" class="list-row">
            <div>
              <strong>{{ w.name }}</strong>
              <small>{{ w.site || `счётчик ${w.counter_id}` }}</small>
            </div>
          </div>
        </div>
        <p v-else-if="step.status !== 'blocked'" class="empty">
          Проекты появятся по счётчикам Метрики. Если на тарифе есть лимит проектов, выберите сайты ниже.
        </p>
        <ProjectChoice v-if="step.status !== 'blocked'" @changed="load" />
      </template>

      <!-- 4. Загрузка -->
      <template v-else-if="step.id === 'load'">
        <p v-if="step.done">
          Кабинеты загружены. Свежая статистика будет догружаться сама. Подробности — в
          <RouterLink to="/cabinet/connections">подключениях</RouterLink>.
        </p>
        <template v-else-if="step.status !== 'blocked'">
          <p class="welcome-wait">
            <span v-if="step.status === 'progress'" class="spinner" aria-hidden="true"></span>
            Загрузка идёт на сервере — можно перейти к следующему шагу, данные появятся сами.
          </p>
          <RouterLink class="btn btn--ghost" to="/cabinet/connections">Что загружается</RouterLink>
        </template>
      </template>

      <!-- 5. Первый вопрос ИИ -->
      <template v-else-if="step.id === 'ai'">
        <p v-if="step.done">Первый вопрос задан. Предложения ИИ, которые ждут вашего решения, — в шапке «Ждёт решения».</p>
        <div v-if="step.status !== 'blocked'" class="welcome-questions">
          <RouterLink
            v-for="q in QUESTIONS"
            :key="q.ask"
            class="welcome-question"
            :to="{ path: '/cabinet/chat', query: { ask: q.ask } }"
          >
            <strong>{{ q.title }}</strong>
            <small>{{ q.text }}</small>
          </RouterLink>
        </div>
      </template>

      <div class="form-actions welcome-nav">
        <button v-if="prevStep" class="btn btn--ghost" type="button" @click="selected = prevStep.id">Назад</button>
        <button v-if="nextStep" class="btn btn--accent" type="button" @click="selected = nextStep.id">Дальше</button>
        <RouterLink v-else class="btn btn--accent" to="/cabinet">Открыть Сводку</RouterLink>
        <button v-if="!wizard.complete" class="btn btn--ghost welcome-skip" type="button" :disabled="busy" @click="skip">
          Пропустить мастер
        </button>
      </div>
    </section>

    <section v-if="wizard.complete && extras.length" class="panel">
      <h2>Что ещё настроить</h2>
      <p class="hint">Необязательно, но с этим помощник точнее: цели, ссылки и уведомления.</p>
      <ol class="checklist">
        <li v-for="s in extras" :key="s.id">
          <span class="checklist__mark" aria-hidden="true">·</span>
          <div>
            <strong>{{ s.title }}</strong>
            <small>{{ s.hint }}</small>
          </div>
          <RouterLink v-if="s.to && s.status !== 'blocked'" class="btn btn--ghost" :to="s.to">Открыть</RouterLink>
        </li>
      </ol>
    </section>
  </template>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { Check } from '@lucide/vue'
import ProjectChoice from '../components/ProjectChoice.vue'
import YandexConnectButton from '../components/YandexConnectButton.vue'
import { errorText, webApi } from '../api/web'
import { loadProfile, useProfile } from '../composables/useProfile'
import { pushToast } from '../composables/useToasts'
import { loadWorkspaces, useWorkspaces } from '../composables/useWorkspaces'
import { useHead } from '../utils/useHead'

useHead('Начало работы — AiMarketing', true)

const POLL_MS = 10000
const STATUS_CHIPS = {
  done: { text: 'готово', cls: 'chip--ok' },
  todo: { text: 'нужно сделать', cls: '' },
  progress: { text: 'идёт', cls: 'chip--info' },
  error: { text: 'ошибка', cls: 'chip--bad' },
  blocked: { text: 'позже', cls: '' },
}
const DETAIL_TONES = { error: 'tone-bad', progress: 'tone-warn' }
const QUESTIONS = [
  {
    ask: 'first',
    title: 'С чего начать?',
    text: 'Где теряются деньги и какие три действия дадут результат быстрее всего',
  },
  {
    ask: 'cleanup',
    title: 'Какие запросы сливают бюджет?',
    text: 'Запросы с расходом без конверсий и минус-фразы для них',
  },
  {
    ask: 'kpi',
    title: 'Что настроить в проектах?',
    text: 'Цели Метрики, KPI и бюджеты, которых не хватает для оценки',
  },
]

const router = useRouter()
const { user } = useProfile()
const { workspaces } = useWorkspaces()
const state = ref(null)
const loadError = ref('')
const busy = ref(false)
const selected = ref('')
let timer = 0

const wizard = computed(() => state.value?.wizard || { steps: [], current: null, complete: false })
const steps = computed(() => wizard.value.steps)
const step = computed(() => steps.value.find((s) => s.id === selected.value) || steps.value[0] || {})
const index = computed(() => steps.value.findIndex((s) => s.id === step.value.id))
const prevStep = computed(() => (index.value > 0 ? steps.value[index.value - 1] : null))
const nextStep = computed(() => steps.value[index.value + 1] || null)
const activeProjects = computed(() => workspaces.value.filter((w) => !w.archived && !w.locked))
// После мастера — необязательные шаги прежнего чек-листа, которые ещё не сделаны.
const extras = computed(() =>
  (state.value?.steps || []).filter((s) => !s.done && ['campaigns', 'link_template', 'goals', 'telegram'].includes(s.id)),
)

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

watch(selected, (id) => {
  if (id === 'projects') loadWorkspaces(true).catch(() => null)
})

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
.welcome-connect {
  display: grid;
  gap: 0.75rem;
  justify-items: start;
}

.welcome-connect .hint {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
  margin: 0;
}

.welcome-wait {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}

.welcome-questions {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 0.75rem;
}

.welcome-question {
  display: grid;
  gap: 0.3rem;
  padding: 0.9rem 1rem;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  color: var(--ink);
  text-decoration: none;
  background: var(--surface);
  transition: border-color 0.15s ease, background 0.15s ease;
}

.welcome-question:hover {
  border-color: var(--accent);
  background: var(--accent-soft);
}

.welcome-question small {
  color: var(--muted);
}

.welcome-nav {
  margin-top: 1.25rem;
}

.welcome-skip {
  margin-left: auto;
}
</style>
