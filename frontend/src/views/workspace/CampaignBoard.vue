<template>
  <div ref="scroller" class="board panel" aria-label="Доска кампании">
    <header class="board__head">
      <div class="board__title">
        <h3>{{ build.draft?.name || 'Новая кампания' }}</h3>
        <p class="hint">
          Доска сборки · готово {{ doneCount }} из {{ steps.length }}
          <template v-if="locked"> · черновик {{ build.draft.status === 'done' ? 'создан' : 'создаётся' }}, правки закрыты</template>
        </p>
      </div>
      <nav class="board__steps" aria-label="Этапы">
        <button
          v-for="(s, i) in steps"
          :key="s.key"
          type="button"
          class="board__step"
          :class="`is-${s.status}`"
          :title="STATUS[s.status] || s.status"
          @click="show(s.section)"
        >
          <span class="board__dot">{{ s.status === 'done' ? '✓' : i + 1 }}</span>
          {{ s.label }}
        </button>
      </nav>
    </header>
    <p v-if="!locked" class="hint board__legend">
      <span class="board__swatch" aria-hidden="true" /> предложено ИИ и ждёт решения. Заголовок, текст или фразу
      можно поправить щелчком — ИИ увидит правки.
    </p>

    <BoardSection name="goals" title="Цели" :stages="stagesOf('goals')" :tables="tables.goals">
      <BoardFacts v-if="build.goals.length" :items="build.goals" />
      <p v-else class="hint">ИИ спросит про продукт, гео, бюджет и цель — согласованное появится здесь.</p>
    </BoardSection>

    <BoardSection name="demand" title="Спрос" :stages="stagesOf('demand')" :tables="tables.demand">
      <BoardFacts v-if="build.directions.length" :items="build.directions" bars />
      <p v-else class="hint">Направления с частотностью из Вордстата появятся после анализа спроса.</p>
    </BoardSection>

    <BoardSection name="groups" title="Структура и объявления" :stages="stagesOf('groups')" :tables="tables.groups">
      <template #tools>
        <small v-if="build.groups.length" class="board-section__meta">
          {{ build.groups.length }} {{ plural(build.groups.length, 'группа', 'группы', 'групп') }} · {{ keywordCount }}
          {{ plural(keywordCount, 'фраза', 'фразы', 'фраз') }}<template v-if="audienceCount">
            · {{ audienceCount }} {{ plural(audienceCount, 'аудитория', 'аудитории', 'аудиторий') }}</template><template v-if="imageCount">
            · {{ imageCount }} {{ plural(imageCount, 'картинка', 'картинки', 'картинок') }}</template>
        </small>
        <div class="seg board__preview-mode" role="group" aria-label="Предпросмотр">
          <button
            v-for="m in PREVIEW_MODES"
            :key="m.key"
            type="button"
            :class="{ active: preview === m.key }"
            :aria-pressed="preview === m.key"
            @click="preview = m.key"
          >
            {{ m.label }}
          </button>
        </div>
      </template>
      <p v-if="!build.groups.length" class="hint">
        Группы с фразами появятся на этапе «Группы и фразы», аудитории (ретаргетинг, LAL, интересы и привычки) — на
        этапе «Аудитории», объявления — на этапе «Объявления», картинки — на этапе «Картинки».
      </p>
      <BoardGroup
        v-for="g in build.groups"
        :key="g.name"
        :group="g"
        :names="groupNames"
        :site="site"
        :preview="preview"
        :sitelinks="build.sitelinks"
        :callouts="build.callouts"
        :ads-started="adsStarted"
      />
    </BoardSection>

    <BoardSection name="negatives" title="Минус-фразы" :stages="stagesOf('negatives')" :tables="tables.negatives">
      <ul v-if="build.negatives.length" class="board-chips">
        <li
          v-for="n in build.negatives"
          :key="n.text"
          :class="{ 'is-pending': n.pending, 'is-off': !isOn(n), 'is-own': n.own }"
        >
          <input
            v-if="n.pending"
            type="checkbox"
            :checked="isOn(n)"
            :disabled="disabled"
            :aria-label="n.text"
            @change="toggle(n)"
          />
          <span class="board-chips__text">−{{ n.text }}</span>
          <button
            class="board-x"
            type="button"
            :title="n.pending && !n.added ? 'Снять галочку' : 'Убрать'"
            :aria-label="`Убрать: ${n.text}`"
            :disabled="disabled"
            @click="remove('negative', '', [n])"
          >
            ×
          </button>
        </li>
      </ul>
      <p v-else class="hint">Минус-фразы ИИ предложит вместе с фразами или отдельным этапом.</p>
      <form v-if="build.groups.length" class="board-add" @submit.prevent="addNegative">
        <input
          v-model="negative"
          class="ai-input"
          maxlength="4096"
          placeholder="+ минус-фраза"
          aria-label="Новая минус-фраза"
          :disabled="disabled"
        />
        <button v-if="negative.trim()" class="btn btn--xs" type="submit" :disabled="disabled">Добавить</button>
      </form>
    </BoardSection>

    <BoardSection name="extras" title="Расширения" :stages="stagesOf('extras')" :tables="tables.extras">
      <BoardFacts v-if="build.extras.length" :items="build.extras" />
      <template v-if="build.sitelinks.length">
        <h5 class="board__subhead">Быстрые ссылки</h5>
        <ul class="board-links">
          <li
            v-for="l in build.sitelinks"
            :key="l.text"
            :class="{ 'is-pending': l.pending, 'is-off': !isOn(l) }"
          >
            <input v-if="l.pending" type="checkbox" :checked="isOn(l)" :disabled="disabled" :aria-label="l.text" @change="toggle(l)" />
            <strong>{{ l.text }}</strong>
            <small v-if="l.description">{{ l.description }}</small>
            <small v-if="l.href" class="board-links__href">{{ l.href }}</small>
          </li>
        </ul>
      </template>
      <template v-if="build.callouts.length">
        <h5 class="board__subhead">Уточнения</h5>
        <ul class="board-chips">
          <li v-for="c in build.callouts" :key="c.text" :class="{ 'is-pending': c.pending, 'is-off': !isOn(c) }">
            <input v-if="c.pending" type="checkbox" :checked="isOn(c)" :disabled="disabled" :aria-label="c.text" @change="toggle(c)" />
            <span class="board-chips__text">{{ c.text }}</span>
          </li>
        </ul>
      </template>
      <p v-if="!build.extras.length && !build.sitelinks.length && !build.callouts.length" class="hint">
        Быстрые ссылки и уточнения — после объявлений. С ними объявление крупнее и заметнее в выдаче.
      </p>
    </BoardSection>

    <BoardSection name="settings" title="Настройки" :stages="stagesOf('settings')" :tables="tables.settings">
      <BoardFacts v-if="build.settings.length" :items="build.settings" />
      <p v-else class="hint">Стратегия, бюджет, гео и расписание — последним этапом перед черновиком.</p>
    </BoardSection>

    <BoardSection name="ready" title="Готовность" :stages="draftStage">
      <ul class="board-checks">
        <li v-for="c in build.checks" :key="c.key + c.label" :class="`is-${c.state}`">
          <span class="board-checks__icon" aria-hidden="true">{{ CHECK_ICONS[c.state] }}</span>
          <span>{{ c.label }}</span>
          <small v-if="c.note">{{ c.note }}</small>
        </li>
      </ul>

      <div v-if="build.draft" class="board-draft" :class="`is-${build.draft.status}`" :data-action="build.draft.action_id">
        <template v-if="build.draft.status === 'pending'">
          <strong>Черновик готов к созданию</strong>
          <p v-if="build.draft.summary" class="hint">{{ build.draft.summary }}</p>
          <p v-if="!build.ready" class="hint tone-bad">
            В проверке есть красные пункты — Директ может не принять кампанию. Поправьте на доске или напишите ИИ.
          </p>
          <div class="board-decision__buttons">
            <button class="btn btn--accent" type="button" :disabled="disabled" @click="emit('draft', true)">
              Создать черновик в Директе
            </button>
            <button class="btn btn--ghost" type="button" :disabled="disabled" @click="emit('draft', false)">Отклонить</button>
          </div>
          <p class="hint">Черновик сам не запустится: на модерацию его отправляете вы.</p>
        </template>
        <template v-else-if="build.draft.status === 'done'">
          <strong>Черновик создан в Директе</strong>
          <RouterLink v-if="build.draft.campaign_row_id" class="btn" :to="`/w/${wsId}/campaigns/${build.draft.campaign_row_id}`">
            Открыть кампанию
          </RouterLink>
        </template>
        <template v-else-if="build.draft.status === 'error'">
          <strong class="tone-bad">Черновик не создан</strong>
          <p v-if="build.draft.error" class="hint">{{ build.draft.error }}</p>
        </template>
        <strong v-else><span class="spinner" /> Черновик создаётся…</strong>
      </div>
      <p v-else class="hint">
        Когда этапы будут согласованы, ИИ подготовит черновик — создать его в Директе можно будет здесь.
      </p>
    </BoardSection>
  </div>
</template>

<script setup>
/**
 * Доска сборки новой кампании рядом с чатом: разделы по этапам сверху вниз, галочки и решения по
 * ждущим таблицам, правки маркетолога (фразы, группы, заголовки, тексты и картинки), предпросмотр и проверка
 * готовности. Данные — build из get_chat; правки уходят через edit(ops) и возвращают чат целиком.
 */
import { computed, nextTick, onMounted, provide, reactive, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { aiApi } from '../../api/web'
import { plural } from '../../utils/format'
import BoardFacts from './BoardFacts.vue'
import BoardGroup from './BoardGroup.vue'
import BoardSection from './BoardSection.vue'

const props = defineProps({
  build: { type: Object, required: true },
  // Действия чата: ждущие таблицы этапов — с их строками и пояснением.
  actions: { type: Array, default: () => [] },
  site: { type: String, default: '' },
  wsId: { type: [Number, String], required: true },
  disabled: { type: Boolean, default: false },
  // async (ops) => true, если правка прошла.
  edit: { type: Function, required: true },
  // Генерация картинок подключена в настройках.
  imageGen: { type: Boolean, default: false },
})
const emit = defineEmits(['decide', 'draft'])

const SECTION_OF = {
  goals: 'goals',
  analysis: 'demand',
  keywords: 'groups',
  ads: 'groups',
  negatives: 'negatives',
  audiences: 'groups',
  images: 'groups',
  extras: 'extras',
  settings: 'settings',
}
const SHORT = { keywords: 'фразы', audiences: 'аудитории', ads: 'объявления', images: 'картинки' }
const STATUS = {
  done: 'согласовано',
  pending: 'ждёт решения',
  todo: 'впереди',
  skipped: 'пропущено',
  rejected: 'на доработке',
}
const DRAFT_TEXT = { todo: 'впереди', pending: 'ждёт решения', approved: 'создаётся', done: 'создан', error: 'ошибка' }
const CHECK_ICONS = { ok: '✓', warn: '!', bad: '×' }
const PREVIEW_MODES = [
  { key: 'desktop', label: 'Компьютер' },
  { key: 'mobile', label: 'Телефон' },
  { key: 'both', label: 'Оба' },
]
const PREVIEW_KEY = 'board-preview'

const scroller = ref(null)
const busy = ref(false)
const negative = ref('')
const preview = ref(localStorage.getItem(PREVIEW_KEY) || 'both')
// { id ждущей таблицы: [номера строк без галочки] } — решение уходит с этими галочками.
const off = reactive({})

watch(preview, (mode) => localStorage.setItem(PREVIEW_KEY, mode))

// Черновик создаётся или создан — сервер правки не примет.
const locked = computed(() => ['approved', 'done'].includes(props.build.draft?.status))
const disabled = computed(() => props.disabled || busy.value || locked.value)

const pendingTables = computed(() => {
  const ids = new Set(props.build.action_ids || [])
  return props.actions.filter((a) => a.tool === 'propose_items' && a.status === 'pending' && ids.has(a.id))
})

const stageLabels = computed(() => Object.fromEntries(props.build.stages.map((s) => [s.key, s.label])))

const tables = computed(() => {
  const out = { goals: [], demand: [], groups: [], negatives: [], extras: [], settings: [] }
  for (const a of pendingTables.value) {
    const stage = a.args?.stage
    out[SECTION_OF[stage] || 'goals'].push({ ...a, stageLabel: stageLabels.value[stage] || stage })
  }
  return out
})

const steps = computed(() => {
  const out = props.build.stages.map((s) => ({ key: s.key, label: s.label, status: s.status, section: SECTION_OF[s.key] }))
  const draft = props.build.draft
  const status = !draft ? 'todo' : draft.status === 'done' ? 'done' : draft.status === 'error' ? 'rejected' : 'pending'
  out.push({ key: 'draft', label: 'Черновик', status, section: 'ready' })
  return out
})
const doneCount = computed(() => steps.value.filter((s) => s.status === 'done').length)

const draftStage = computed(() => {
  const draft = props.build.draft
  const status = draft ? draft.status : 'todo'
  return [{ key: 'draft', short: '', status, text: DRAFT_TEXT[status] }]
})

const groupNames = computed(() => props.build.groups.map((g) => g.name))
const keywordCount = computed(() => props.build.groups.reduce((n, g) => n + g.keywords.filter(isOn).length, 0))
const audienceCount = computed(() => props.build.groups.reduce((n, g) => n + (g.audiences || []).filter(isOn).length, 0))
const imageCount = computed(() => props.build.groups.reduce((n, g) => n + (g.images || []).filter(isOn).length, 0))
const adsStarted = computed(() => props.build.stages.some((s) => s.key === 'ads' && s.status !== 'todo'))

// Раздел, о котором сейчас речь: последняя ждущая таблица или карточка создания.
const active = computed(() => {
  if (props.build.draft?.status === 'pending') return 'ready'
  const last = pendingTables.value[pendingTables.value.length - 1]
  return last ? SECTION_OF[last.args?.stage] || null : null
})

watch(
  pendingTables,
  (list) => {
    for (const a of list) {
      if (!(a.id in off)) off[a.id] = (a.args?.rows || []).filter((r) => r.checked === false).map((r) => r.n)
    }
  },
  { immediate: true },
)

watch(active, (key) => key && nextTick(() => show(key)))
onMounted(() => {
  if (!active.value) return
  show(active.value, false)
  // Шрифты догружаются и меняют высоту разделов — встать к разделу ещё раз.
  document.fonts?.ready.then(() => active.value && show(active.value, false))
})

function stagesOf(section) {
  return props.build.stages
    .filter((s) => SECTION_OF[s.key] === section)
    .map((s) => ({ key: s.key, short: SHORT[s.key] || s.label, status: s.status }))
}

function isOn(item) {
  if (!item.pending) return item.checked !== false
  const list = off[item.action_id]
  return list ? !list.includes(item.n) : item.checked !== false
}

function toggle(item) {
  setOn(item, !isOn(item))
}

function setOn(item, on) {
  const list = off[item.action_id] || []
  off[item.action_id] = on ? list.filter((n) => n !== item.n) : [...new Set([...list, item.n])]
}

function offOf(table) {
  return off[table.id] || []
}

function setOff(id, list) {
  off[id] = [...list]
}

async function edit(ops) {
  if (disabled.value) return false
  busy.value = true
  try {
    return await props.edit(ops)
  } finally {
    busy.value = false
  }
}

/** Убрать с доски: предложенное ИИ в ждущей таблице — снять галочку, остальное — правкой. */
async function remove(kind, group, items) {
  const ops = []
  for (const item of items) {
    if (item.pending && !item.added) setOn(item, false)
    // Картинка — по хэшу; подпись — для заметки ИИ.
    else if (kind === 'image') ops.push({ op: 'remove', kind, group, text: item.hash, name: item.text })
    else ops.push({ op: 'remove', kind, group, text: item.text })
  }
  return ops.length ? edit(ops) : true
}

async function addNegative() {
  const text = negative.value.trim()
  if (text && (await edit([{ op: 'add', kind: 'negative', text }]))) negative.value = ''
}

function decide(id, payload) {
  emit('decide', id, payload)
}

// Картинки: кабинет кампании (или выбранный, пока ИИ его не назвал), библиотека кампаний — один раз на доску.
const chosenAccount = ref(null)
let libraryLoad = null

function library() {
  libraryLoad ??= aiApi.wsImages(props.wsId).then(
    (r) => r.items || [],
    (err) => {
      libraryLoad = null
      throw err
    },
  )
  return libraryLoad
}

const images = {
  wsId: () => props.wsId,
  account: computed(() => props.build.account_id ?? chosenAccount.value),
  accounts: computed(() => props.build.accounts || []),
  chooseAccount: (id) => {
    chosenAccount.value = id
  },
  canGenerate: computed(() => props.imageGen),
  library,
}

provide('board', { isOn, toggle, remove, edit, disabled, offOf, setOff, decide, images })

/** Прокрутить доску к разделу и подсветить его. На узком экране доска не прокручивается сама — страницу. */
function show(section, smooth = true) {
  const box = scroller.value
  const el = section && box?.querySelector(`[data-section="${section}"]`)
  if (!el || !el.offsetParent) return
  const behavior = smooth ? 'smooth' : 'auto'
  if (box.scrollHeight > box.clientHeight + 1) {
    // Шапка доски с этапами закреплена сверху — раздел должен встать под неё.
    const head = box.querySelector('.board__head')?.offsetHeight || 0
    const top = el.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop - head - 8
    box.scrollTo({ top: Math.max(0, top), behavior })
  } else {
    el.scrollIntoView({ behavior, block: 'start' })
  }
  if (!smooth) return
  el.classList.remove('is-flash')
  void el.offsetWidth
  el.classList.add('is-flash')
  setTimeout(() => el.classList.remove('is-flash'), 1600)
}

/** Раздел доски, где решается действие чата (таблица этапа или карточка создания). */
function showAction(actionId) {
  if (props.build.draft?.action_id === actionId) return show('ready')
  const action = props.actions.find((a) => a.id === actionId)
  show(SECTION_OF[action?.args?.stage] || 'goals')
}

defineExpose({ show, showAction })
</script>
