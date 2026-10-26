<template>
  <section class="panel">
    <div class="panel__head">
      <div>
        <h2>Пространство</h2>
        <p class="hint">
          Счётчик Метрики № {{ ws.counter_id }}{{ ws.counter_name ? ` · ${ws.counter_name}` : '' }}{{
            ws.counter_login ? ` · доступ через ${ws.counter_login}` : ''
          }}
        </p>
      </div>
    </div>
    <p v-if="!ws.counter_ok" class="notice notice--warn">
      Счётчик не найден среди включённых в подключениях Яндекса — статистика сайта не обновляется. Проверьте доступы в
      <RouterLink to="/cabinet/accounts">аккаунтах</RouterLink>.
    </p>
    <form class="form-grid" @submit.prevent="saveGeneral">
      <div class="field">
        <label>Название</label>
        <input v-model.trim="general.name" type="text" maxlength="120" required />
      </div>
      <div class="field">
        <label>Сайт</label>
        <input v-model.trim="general.site" type="text" maxlength="300" placeholder="example.ru" />
      </div>
      <div class="field">
        <label>Папка клиента</label>
        <select v-model="general.client_id">
          <option :value="null">Без клиента</option>
          <option v-for="c in clients" :key="c.id" :value="c.id">{{ c.name }}</option>
        </select>
        <small class="field-hint"><RouterLink to="/cabinet/clients">Папки клиентов</RouterLink></small>
      </div>
      <div class="field">
        <label>Бюджет на месяц, ₽</label>
        <input v-model="general.monthly_budget_rub" type="number" min="0" step="100" placeholder="не задан" />
        <small class="field-hint">Сводка покажет темп расхода относительно плана.</small>
      </div>
      <div class="field">
        <label>KPI</label>
        <select v-model="general.kpi_type">
          <option value="">Не задан</option>
          <option value="cpa">CPA — цена конверсии, ₽</option>
          <option value="drr">ДРР — доля рекламных расходов, %</option>
        </select>
      </div>
      <div class="field">
        <label>Целевое значение {{ general.kpi_type === 'drr' ? ', %' : general.kpi_type === 'cpa' ? ', ₽' : '' }}</label>
        <input
          v-model="general.kpi_target"
          type="number"
          min="0"
          step="any"
          :disabled="!general.kpi_type"
          :placeholder="general.kpi_type ? '' : 'сначала выберите KPI'"
        />
      </div>
      <div class="field field--wide">
        <label>Основная цель Метрики</label>
        <div class="filters--inline">
          <select v-model="general.goal_id">
            <option :value="null">Все цели (сумма конверсий)</option>
            <option v-for="g in ws.goals" :key="g.goal_id" :value="g.goal_id">{{ g.name }} · № {{ g.goal_id }}</option>
          </select>
          <button class="btn btn--ghost" type="button" :disabled="goalsBusy || !ws.counter_row_id" @click="syncGoals">
            {{ goalsBusy ? 'Обновляем…' : 'Обновить цели' }}
          </button>
        </div>
        <small class="field-hint">Конверсии, CPA и ДРР в сводке и аналитике считаются по этой цели.</small>
      </div>
      <p v-if="generalError" class="form-error field--wide">{{ generalError }}</p>
      <div class="form-actions field--wide">
        <button class="btn btn--accent" type="submit" :disabled="generalBusy || !generalChanged">
          {{ generalBusy ? 'Сохраняем…' : 'Сохранить' }}
        </button>
      </div>
    </form>
  </section>

  <section class="panel">
    <div class="panel__head">
      <div>
        <h2>Кабинеты Директа</h2>
        <p class="hint">
          Кабинеты, из которых пространство берёт кампании и в которых создаёт новые. Новые логины Яндекса добавляются в
          <RouterLink to="/cabinet/accounts">аккаунтах</RouterLink>.
        </p>
      </div>
    </div>
    <p v-if="accountsError" class="empty">{{ accountsError }}</p>
    <p v-else-if="!linked.length" class="empty">Кабинеты не подключены.</p>
    <div v-else class="list">
      <div v-for="a in linked" :key="a.id" class="list-row" :class="{ 'is-off': !a.enabled }">
        <div>
          <strong>
            {{ a.name || a.client_login }}
            <span v-if="a.sandbox" class="chip">песочница</span>
            <span v-if="!a.enabled" class="chip">выключен</span>
          </strong>
          <small>
            {{ a.client_login }}<template v-if="a.connection_login"> · вход {{ a.connection_login }}</template> ·
            кампаний в пространстве {{ a.campaigns_in_workspace }} из {{ a.campaigns_total }}
            <template v-if="a.balance_kopecks != null"> · баланс {{ rub(a.balance_kopecks) }}</template>
          </small>
          <small v-if="a.counter_access?.hint" :class="COUNTER_TONES[a.counter_access.status]">
            {{ a.counter_access.hint }}
          </small>
        </div>
        <button class="btn btn--ghost btn--danger" type="button" :disabled="accountBusy === a.id" @click="removeAccount(a)">
          Отключить
        </button>
      </div>
    </div>

    <div v-if="available.length" class="add-account">
      <h3>Подключить кабинет</h3>
      <form class="filters filters--inline" @submit.prevent="addAccount">
        <select v-model="addForm.account_id" required>
          <option :value="null" disabled>Кабинет…</option>
          <option v-for="a in available" :key="a.id" :value="a.id" :disabled="!a.enabled">
            {{ a.name || a.client_login }}{{ a.sandbox ? ' (песочница)' : '' }} · кампаний {{ a.campaigns_total
            }}{{ a.campaigns_matching ? `, со счётчиком ${a.campaigns_matching}` : '' }}
          </option>
        </select>
        <select v-model="addForm.campaigns">
          <option value="matching">Забрать кампании с этим счётчиком</option>
          <option value="all">Забрать все свободные кампании</option>
          <option value="none">Без кампаний</option>
        </select>
        <button class="btn btn--accent" type="submit" :disabled="!addForm.account_id || accountBusy === 'add'">
          {{ accountBusy === 'add' ? 'Подключаем…' : 'Подключить' }}
        </button>
      </form>
    </div>
  </section>

  <section class="panel">
    <div class="panel__head">
      <div>
        <h2>Кампании пространства</h2>
        <p class="hint">
          Отметьте кампании, с которыми работает пространство. Кампания живёт в одном пространстве — отметка здесь
          перенесёт её из другого.
        </p>
      </div>
      <div class="row-actions">
        <button class="btn btn--ghost" type="button" :disabled="syncBusy" @click="syncCampaigns">
          {{ syncBusy ? syncText || 'Обновляем…' : 'Обновить из Директа' }}
        </button>
      </div>
    </div>

    <div class="filters filters--inline">
      <input v-model.trim="campaignSearch" class="search-input" type="search" placeholder="Название или № кампании" />
      <label class="check-row">
        <input v-model="onlyMatching" type="checkbox" />
        <span>Только со счётчиком № {{ ws.counter_id }}</span>
      </label>
      <label class="check-row">
        <input v-model="showArchived" type="checkbox" />
        <span>Показывать архивные</span>
      </label>
    </div>

    <p v-if="candidatesLoading && !groups.length" class="empty">Загружаем кампании…</p>
    <p v-else-if="candidatesError" class="empty">{{ candidatesError }}</p>
    <p v-else-if="!groups.length" class="empty">
      В подключённых логинах нет кампаний Директа. Обновите кабинеты в
      <RouterLink to="/cabinet/accounts">аккаунтах</RouterLink>.
    </p>
    <template v-else>
      <div v-for="g in visibleGroups" :key="g.account_id" class="campaign-picker">
        <div class="campaign-picker__head">
          <label class="check-row">
            <input
              type="checkbox"
              :checked="g.items.length > 0 && g.items.every((c) => selected.has(c.id))"
              :indeterminate.prop="g.items.some((c) => selected.has(c.id)) && !g.items.every((c) => selected.has(c.id))"
              @change="toggleGroup(g, $event.target.checked)"
            />
            <strong>{{ g.account_name }}</strong>
          </label>
          <small>
            {{ g.client_login }}<template v-if="g.sandbox"> · песочница</template>
            <template v-if="!g.linked"> · кабинет подключится автоматически</template>
          </small>
        </div>
        <label v-for="c in g.items" :key="c.id" class="campaign-picker__row" :class="{ 'is-off': c.state === 'ARCHIVED' }">
          <input type="checkbox" :checked="selected.has(c.id)" @change="toggle(c.id, $event.target.checked)" />
          <span>
            {{ c.name }}
            <small>
              № {{ c.campaign_id }} · {{ CAMPAIGN_STATES[c.state] || c.state || '—' }} ·
              {{ CAMPAIGN_TYPES[c.type] || c.type }}
              <template v-if="!c.matches_counter">
                · <span class="tone-warn">{{ c.counter_ids.length ? `счётчик ${c.counter_ids.join(', ')}` : 'без счётчика' }}</span>
              </template>
              <template v-if="c.workspace_id && !c.in_this"> · сейчас в «{{ c.workspace_name }}»</template>
            </small>
          </span>
        </label>
      </div>
      <p v-if="!visibleGroups.length" class="empty">Под фильтр ничего не подходит.</p>
    </template>

    <div v-if="diff.add.length || diff.remove.length" class="form-actions form-actions--sticky">
      <span>
        <template v-if="diff.add.length">Добавить {{ diff.add.length }}</template>
        <template v-if="diff.add.length && diff.remove.length">, </template>
        <template v-if="diff.remove.length">убрать {{ diff.remove.length }}</template>
        <template v-if="diff.moving"> (перенос из других пространств: {{ diff.moving }})</template>
      </span>
      <button class="btn btn--ghost" type="button" :disabled="campaignsBusy" @click="resetSelection">Сбросить</button>
      <button class="btn btn--accent" type="button" :disabled="campaignsBusy" @click="saveCampaigns">
        {{ campaignsBusy ? 'Сохраняем…' : 'Сохранить состав' }}
      </button>
    </div>
  </section>

  <section id="ai" class="panel">
    <div class="panel__head">
      <div>
        <h2>Доступ ИИ</h2>
        <p class="hint">
          Что ИИ-помощник может менять в Директе сам. Всё остальное он предлагает в чате, и действие выполняется только
          после вашего «Выполнить». Подключение модели — в <RouterLink to="/cabinet/settings">настройках аккаунта</RouterLink>.
        </p>
      </div>
    </div>
    <form class="ai-access" @submit.prevent="saveAi">
      <label class="check-row ai-access__full">
        <input v-model="ai.full_access" type="checkbox" />
        <span>
          <strong>Полный доступ</strong>
          <small>ИИ выполняет любые действия в рамках пространства без согласования. Удаление — тоже.</small>
        </span>
      </label>
      <fieldset class="ai-access__cats" :disabled="ai.full_access">
        <legend>Без согласования разрешено</legend>
        <label v-for="c in ws.ai_categories" :key="c.key" class="check-row">
          <input v-model="ai.categories[c.key]" type="checkbox" />
          <span>{{ c.label }}</span>
        </label>
      </fieldset>
      <div class="field">
        <label>Предел разового изменения бюджета или ставки: {{ ai.max_change_pct }}%</label>
        <input v-model.number="ai.max_change_pct" type="range" min="0" max="200" step="5" :disabled="ai.full_access" />
        <small class="field-hint">
          Изменение больше предела ИИ всё равно отправит на согласование, даже если категория разрешена.
        </small>
      </div>
      <div class="field field--wide">
        <label>Заметки для ИИ</label>
        <textarea
          v-model="ai.notes"
          rows="5"
          maxlength="8000"
          placeholder="Ниша, сезонность, что нельзя трогать, приоритетные регионы, тон объявлений…"
        />
        <small class="field-hint">ИИ читает это перед каждым ответом в чате этого пространства.</small>
      </div>
      <p v-if="aiError" class="form-error">{{ aiError }}</p>
      <div class="form-actions">
        <button class="btn btn--accent" type="submit" :disabled="aiBusy || !aiChanged">
          {{ aiBusy ? 'Сохраняем…' : 'Сохранить' }}
        </button>
      </div>
    </form>
  </section>

  <section class="panel">
    <div class="panel__head">
      <div>
        <h2>{{ ws.archived ? 'Пространство в архиве' : 'Архив' }}</h2>
        <p class="hint">
          {{
            ws.archived
              ? 'Пространство скрыто из переключателя и обзора. Кампании и история сохранены.'
              : 'Архивное пространство скрыто из переключателя и обзора; кампании в Директе не меняются.'
          }}
        </p>
      </div>
      <button class="btn btn--ghost" :class="{ 'btn--danger': !ws.archived }" type="button" :disabled="archiveBusy" @click="toggleArchive">
        {{ ws.archived ? 'Вернуть из архива' : 'В архив' }}
      </button>
    </div>
  </section>
</template>

<script setup>
import { askConfirm } from '../../composables/useConfirm'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { errorText, jobProgressText, webApi, wsApi } from '../../api/web'
import { pushToast } from '../../composables/useToasts'
import { loadWorkspaces, useWorkspaces } from '../../composables/useWorkspaces'
import { CAMPAIGN_STATES, CAMPAIGN_TYPES, plural, rub } from '../../utils/format'
import { useWs } from './context'

const COUNTER_TONES = { ok: 'tone-ok', unknown: 'tone-warn', sandbox: 'tone-warn' }

const { ws, reload, patch } = useWs()
const { clients } = useWorkspaces()

// --- Основное ---------------------------------------------------------------------------------

function generalFromWs(w) {
  return {
    name: w.name || '',
    site: w.site || '',
    client_id: w.client_id ?? null,
    kpi_type: w.kpi_type || '',
    kpi_target: w.kpi_target ?? '',
    monthly_budget_rub: w.monthly_budget_rub ?? '',
    goal_id: w.goal_id ?? null,
  }
}

const general = reactive(generalFromWs(ws.value))
const generalBusy = ref(false)
const generalError = ref('')
const goalsBusy = ref(false)

function numOrNull(value) {
  if (value === '' || value == null) return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

const generalPayload = computed(() => {
  const base = generalFromWs(ws.value)
  const out = {}
  for (const key of ['name', 'site', 'client_id', 'kpi_type', 'goal_id']) {
    if ((general[key] ?? null) !== (base[key] ?? null)) out[key] = general[key]
  }
  const kpiTarget = general.kpi_type ? numOrNull(general.kpi_target) : null
  if (kpiTarget !== numOrNull(base.kpi_target)) out.kpi_target = kpiTarget
  const budget = numOrNull(general.monthly_budget_rub)
  if (budget !== numOrNull(base.monthly_budget_rub)) out.monthly_budget_rub = budget
  return out
})
const generalChanged = computed(() => Object.keys(generalPayload.value).length > 0)

async function saveGeneral() {
  generalError.value = ''
  if (general.kpi_type && !(numOrNull(general.kpi_target) > 0)) {
    generalError.value = 'Укажите целевое значение KPI больше нуля.'
    return
  }
  generalBusy.value = true
  try {
    const updated = await wsApi.update(ws.value.id, generalPayload.value)
    patch(updated)
    Object.assign(general, generalFromWs(updated))
    pushToast('Сохранено', 'ok')
  } catch (err) {
    generalError.value = errorText(err)
  } finally {
    generalBusy.value = false
  }
}

async function syncGoals() {
  goalsBusy.value = true
  try {
    await webApi.syncGoals(ws.value.counter_row_id)
    await reload()
    pushToast(`Цели обновлены: ${ws.value.goals?.length || 0}`, 'ok')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    goalsBusy.value = false
  }
}

// --- Кабинеты ---------------------------------------------------------------------------------

const linked = ref(ws.value.accounts || [])
const candidates = ref([])
const accountsError = ref('')
const accountBusy = ref(null)
const addForm = reactive({ account_id: null, campaigns: 'matching' })
const available = computed(() => candidates.value.filter((a) => !a.linked))

async function loadAccounts() {
  accountsError.value = ''
  try {
    const [own, all] = await Promise.all([wsApi.accounts(ws.value.id), wsApi.accountCandidates(ws.value.id)])
    linked.value = own.items || []
    candidates.value = all.items || []
    if (addForm.account_id && !available.value.some((a) => a.id === addForm.account_id)) addForm.account_id = null
  } catch (err) {
    accountsError.value = errorText(err)
  }
}

async function afterStructureChange() {
  await Promise.all([loadAccounts(), loadCandidates(), reload()])
  loadWorkspaces(true).catch(() => {})
}

async function addAccount() {
  accountBusy.value = 'add'
  try {
    const res = await wsApi.addAccount(ws.value.id, addForm.account_id, addForm.campaigns)
    pushToast(`Кабинет подключён, кампаний добавлено: ${res.campaigns_added ?? 0}`, 'ok')
    addForm.account_id = null
    await afterStructureChange()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    accountBusy.value = null
  }
}

async function removeAccount(a) {
  if (
    !await askConfirm(
      `Отключить кабинет ${a.client_login}? Его кампании уйдут из пространства (в Директе ничего не изменится).`,
    )
  )
    return
  accountBusy.value = a.id
  try {
    const res = await wsApi.removeAccount(ws.value.id, a.id)
    pushToast(`Кабинет отключён, кампаний убрано: ${res.campaigns_removed ?? 0}`, 'ok')
    await afterStructureChange()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    accountBusy.value = null
  }
}

// --- Кампании ---------------------------------------------------------------------------------

const groups = ref([])
const candidatesLoading = ref(false)
const candidatesError = ref('')
const selected = ref(new Set())
const initial = ref(new Set())
const campaignSearch = ref('')
const onlyMatching = ref(false)
const showArchived = ref(false)
const campaignsBusy = ref(false)
const syncBusy = ref(false)
const syncText = ref('')

async function loadCandidates() {
  candidatesLoading.value = true
  candidatesError.value = ''
  try {
    const data = await wsApi.campaignCandidates(ws.value.id)
    groups.value = data.items || []
    const inThis = new Set()
    for (const g of groups.value) for (const c of g.campaigns) if (c.in_this) inThis.add(c.id)
    initial.value = inThis
    selected.value = new Set(inThis)
  } catch (err) {
    candidatesError.value = errorText(err)
  } finally {
    candidatesLoading.value = false
  }
}

const visibleGroups = computed(() => {
  const q = campaignSearch.value.toLowerCase()
  return groups.value
    .map((g) => ({
      ...g,
      items: g.campaigns.filter((c) => {
        if (selected.value.has(c.id) || initial.value.has(c.id)) {
          // Отмеченные видны всегда, кроме поиска.
        } else {
          if (!showArchived.value && c.state === 'ARCHIVED') return false
          if (onlyMatching.value && !c.matches_counter) return false
        }
        if (q && !c.name.toLowerCase().includes(q) && !String(c.campaign_id).includes(q)) return false
        return true
      }),
    }))
    .filter((g) => g.items.length)
})

const diff = computed(() => {
  const add = [...selected.value].filter((id) => !initial.value.has(id))
  const remove = [...initial.value].filter((id) => !selected.value.has(id))
  const addSet = new Set(add)
  let moving = 0
  for (const g of groups.value) for (const c of g.campaigns) if (addSet.has(c.id) && c.workspace_id) moving += 1
  return { add, remove, moving }
})

function toggle(id, on) {
  const next = new Set(selected.value)
  if (on) next.add(id)
  else next.delete(id)
  selected.value = next
}

function toggleGroup(group, on) {
  const next = new Set(selected.value)
  for (const c of group.items) {
    if (on) next.add(c.id)
    else next.delete(c.id)
  }
  selected.value = next
}

function resetSelection() {
  selected.value = new Set(initial.value)
}

async function saveCampaigns() {
  const { add, remove, moving } = diff.value
  if (
    moving &&
    !(await askConfirm(
      `${moving} ${plural(moving, 'кампания', 'кампании', 'кампаний')} сейчас ${moving === 1 ? 'в другом пространстве' : 'в других пространствах'}. Перенести сюда? Там ${moving === 1 ? 'она пропадёт' : 'они пропадут'} из списка.`,
      { confirmText: 'Перенести', danger: false },
    ))
  )
    return
  campaignsBusy.value = true
  try {
    const res = await wsApi.setCampaigns(ws.value.id, add, remove)
    pushToast(
      `Добавлено ${res.added}${res.moved ? ` (перенесено ${res.moved})` : ''}, убрано ${res.removed}`,
      'ok',
    )
    await afterStructureChange()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    campaignsBusy.value = false
  }
}

async function syncCampaigns() {
  syncBusy.value = true
  syncText.value = ''
  try {
    await wsApi.syncCampaigns(ws.value.id, (job) => {
      syncText.value = jobProgressText(job)
    })
    await afterStructureChange()
    pushToast('Кампании обновлены', 'ok')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    syncBusy.value = false
  }
}

// --- Доступ ИИ --------------------------------------------------------------------------------

function aiFromWs(w) {
  const access = w.ai_access || {}
  return {
    full_access: !!access.full_access,
    categories: { ...(access.categories || {}) },
    max_change_pct: access.max_change_pct ?? 30,
    notes: w.ai_notes || '',
  }
}

const ai = reactive(aiFromWs(ws.value))
const aiBusy = ref(false)
const aiError = ref('')

const aiChanged = computed(() => JSON.stringify(aiFromWs(ws.value)) !== JSON.stringify(ai))

async function saveAi() {
  if (ai.full_access && !ws.value.ai_access?.full_access) {
    const ok = await askConfirm(
      'Включить полный доступ? ИИ сможет менять бюджеты, ставки, создавать и удалять кампании без вашего подтверждения.',
    )
    if (!ok) return
  }
  aiBusy.value = true
  aiError.value = ''
  try {
    const updated = await wsApi.update(ws.value.id, {
      ai_access: {
        full_access: ai.full_access,
        categories: ai.categories,
        max_change_pct: ai.max_change_pct,
      },
      ai_notes: ai.notes,
    })
    patch(updated)
    Object.assign(ai, aiFromWs(updated))
    pushToast('Доступ ИИ сохранён', 'ok')
  } catch (err) {
    aiError.value = errorText(err)
  } finally {
    aiBusy.value = false
  }
}

// --- Архив ------------------------------------------------------------------------------------

const archiveBusy = ref(false)

async function toggleArchive() {
  const archived = !ws.value.archived
  if (archived && !await askConfirm(`Отправить «${ws.value.name}» в архив?`)) return
  archiveBusy.value = true
  try {
    const updated = await wsApi.update(ws.value.id, { archived })
    patch(updated)
    loadWorkspaces(true).catch(() => {})
    pushToast(archived ? 'Пространство в архиве' : 'Пространство восстановлено', 'ok')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    archiveBusy.value = false
  }
}

// Пространство перечитали (например, после синхронизации целей) — подтягиваем несохранённые формы,
// только если пользователь их не трогал.
watch(
  () => ws.value,
  (next, prev) => {
    if (!next || !prev) return
    if (JSON.stringify(generalFromWs(prev)) === JSON.stringify(general)) Object.assign(general, generalFromWs(next))
    if (JSON.stringify(aiFromWs(prev)) === JSON.stringify(ai)) Object.assign(ai, aiFromWs(next))
  },
)

onMounted(() => {
  loadWorkspaces().catch(() => {})
  loadAccounts()
  loadCandidates()
})
</script>
