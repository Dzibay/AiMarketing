<template>
  <div class="rules-manager">
    <section class="panel">
      <h2>Новое правило</h2>
      <p class="hint">
        Правило проверяется каждый час. В режиме проверки оно только показывает, что бы сделало; в автоматическом —
        меняет Директ и пишет в журнал действий.
      </p>
      <div class="filters">
        <div class="field">
          <label>Название</label>
          <input v-model="form.name" type="text" placeholder="CPA высокий" />
        </div>
        <div class="field">
          <label>Объект</label>
          <select v-model="form.target">
            <option value="campaign">Кампании</option>
            <option value="keyword">Ключевые фразы</option>
            <option value="query">Поисковые запросы</option>
          </select>
        </div>
        <div class="field">
          <label>Условие</label>
          <select v-model="form.rule_type">
            <option value="cpa_above">CPA выше порога, ₽</option>
            <option value="spend_no_conv">Расход без конверсий, ₽</option>
            <option value="cpc_above">CPC выше порога, ₽</option>
            <option v-if="form.target === 'campaign'" value="daily_budget_pct">Израсходовано % дневного бюджета</option>
          </select>
        </div>
        <div class="field">
          <label>Порог</label>
          <input v-model.number="form.threshold_value" type="number" min="0" step="1" />
        </div>
        <div v-if="form.target === 'campaign'" class="field">
          <label>За сколько дней</label>
          <input v-model.number="form.lookback_days" type="number" min="1" max="30" />
        </div>
        <div v-if="!workspace" class="field">
          <label>Пространство</label>
          <select v-model="form.scope_workspace_id">
            <option value="">Все</option>
            <option v-for="w in workspaces" :key="w.id" :value="String(w.id)">{{ w.name }}</option>
          </select>
        </div>
        <div class="field">
          <label>Кабинет</label>
          <select v-model="form.scope_account_id">
            <option value="">Все</option>
            <option v-for="a in accounts" :key="a.id" :value="String(a.id)">{{ a.name || a.client_login }}</option>
          </select>
        </div>
        <div v-if="form.target === 'campaign'" class="field">
          <label>Конверсии</label>
          <select v-model="form.goal_row_id">
            <option value="">Конверсии Директа</option>
            <option v-for="g in goals" :key="g.id" :value="String(g.id)">
              {{ g.name || `Цель ${g.goal_id}` }}<template v-if="g.counter_name"> · {{ g.counter_name }}</template>
            </option>
          </select>
        </div>
        <div class="field">
          <label>Действие</label>
          <select v-model="form.action">
            <option v-if="form.target !== 'query'" value="pause">Остановить</option>
            <option v-if="form.target !== 'query'" value="resume">Запустить</option>
            <option v-if="form.target === 'campaign'" value="set_budget">Установить дневной бюджет</option>
            <option v-if="form.target === 'keyword'" value="set_bid">Установить ставку</option>
            <option v-if="form.target === 'query'" value="negative_adgroup">В минус-фразы группы</option>
            <option v-if="form.target === 'query'" value="negative_campaign">В минус-фразы кампании</option>
            <option value="notify">Только уведомить</option>
          </select>
        </div>
        <div v-if="form.action === 'set_budget'" class="field">
          <label>Бюджет, ₽</label>
          <input v-model.number="form.action_budget_rub" type="number" min="300" step="1" />
        </div>
        <div v-if="form.action === 'set_bid'" class="field">
          <label>Ставка, ₽</label>
          <input v-model.number="form.action_bid_rub" type="number" min="0.3" step="0.1" />
        </div>
        <label class="check-row">
          <input v-model="form.dry_run" type="checkbox" />
          Только проверка
        </label>
        <button class="btn btn--accent" type="button" :disabled="busy" @click="create">Создать</button>
      </div>
      <p v-if="form.target !== 'campaign'" class="hint">
        Для фраз и запросов нужна статистика структуры: кампания → «Обновить структуру и статистику».
        <template v-if="form.target === 'query'">Типичный сценарий: расход без конверсий → минус-фраза.</template>
      </p>
    </section>

    <section class="panel">
      <h2>Правила</h2>
      <div v-if="rules.length" class="list">
        <div v-for="rule in rules" :key="rule.id" class="list-row" :class="{ 'is-off': !rule.enabled }">
          <div>
            <strong>
              {{ rule.name }}
              <span class="chip" :class="rule.dry_run ? 'chip--info' : 'chip--warning'">
                {{ rule.dry_run ? 'проверка' : 'автоматически' }}
              </span>
            </strong>
            <small>
              {{ TARGETS[rule.target] || 'Кампании' }}: {{ lowerFirst(TYPES[rule.rule_type] || rule.rule_type) }}
              {{ rule.threshold_value }}<template v-if="rule.target === 'campaign'"> за {{ rule.lookback_days }} дн.</template>
              → {{ lowerFirst(ACTIONS[rule.action] || rule.action) }}
              <template v-if="rule.action === 'set_budget' && rule.action_budget_rub"> {{ rubles(rule.action_budget_rub) }}</template>
              <template v-if="rule.action === 'set_bid' && rule.action_bid_rub"> {{ rubles(rule.action_bid_rub, 2) }}</template>
              <template v-if="rule.goal_name"> · цель «{{ rule.goal_name }}»</template>
              <template v-if="!workspace && rule.workspace_name"> · {{ rule.workspace_name }}</template>
              <template v-if="rule.account_name"> · {{ rule.account_name }}</template>
              <template v-if="!rule.enabled"> · выключено</template>
            </small>
          </div>
          <div class="row-actions">
            <button class="btn btn--ghost" type="button" :disabled="busy" @click="run(rule, true)">Проверить</button>
            <button class="btn btn--ghost" type="button" :disabled="busy" @click="run(rule, false)">Применить</button>
            <button class="btn btn--ghost" type="button" :disabled="busy" @click="patch(rule, { enabled: !rule.enabled })">
              {{ rule.enabled ? 'Выключить' : 'Включить' }}
            </button>
            <button class="btn btn--ghost" type="button" :disabled="busy" @click="patch(rule, { dry_run: !rule.dry_run })">
              {{ rule.dry_run ? 'Включить авто' : 'Только проверка' }}
            </button>
            <button class="btn btn--ghost btn--danger" type="button" :disabled="busy" @click="remove(rule)">Удалить</button>
          </div>
        </div>
      </div>
      <p v-else class="empty">Правил пока нет.</p>
    </section>

    <section v-if="lastRun" class="panel">
      <h2>Результат: {{ lastRun.dry_run ? 'проверка' : 'применение' }}</h2>
      <p class="hint">
        Совпало {{ lastRun.matched }}, применено {{ lastRun.applied }} · {{ TARGETS[lastRun.target] || 'Кампании' }} →
        {{ lowerFirst(ACTIONS[lastRun.action] || lastRun.action || '') }}
      </p>
      <div v-if="lastRun.items?.length" class="list">
        <div v-for="item in lastRun.items" :key="`${item.entity}-${item.row_id}`" class="list-row">
          <div>
            <strong>{{ item.name }}</strong>
            <small>
              {{ item.reason }}
              <template v-if="item.campaign_name"> · {{ item.campaign_name }}</template>
              <template v-if="item.adgroup_name"> · {{ item.adgroup_name }}</template>
            </small>
          </div>
        </div>
      </div>
      <p v-else class="empty">Под условие ничего не попало.</p>
    </section>

    <section v-if="runs.length" class="panel">
      <h2>История прогонов</h2>
      <div class="list">
        <div v-for="item in runs.slice(0, 20)" :key="item.id" class="list-row">
          <div>
            <strong>{{ ruleName(item.rule_id) }}</strong>
            <small>
              совпало {{ item.matched }}, применено {{ item.applied }} · {{ item.dry_run ? 'проверка' : 'авто' }} ·
              {{ formatDateTime(item.created_at) }}
            </small>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { askConfirm } from '../composables/useConfirm'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { errorText, webApi, wsApi } from '../api/web'
import { pushToast } from '../composables/useToasts'
import { useWorkspaces } from '../composables/useWorkspaces'
import { formatDateTime, rubles } from '../utils/format'

const props = defineProps({
  // Пространство: правила только его кампаний; без него — все правила пользователя.
  workspace: { type: Object, default: null },
})

// «CPA выше» остаётся с заглавными, «Остановить» → «остановить».
function lowerFirst(text) {
  const s = String(text || '')
  return /^\p{Lu}{2}/u.test(s) ? s : s.charAt(0).toLowerCase() + s.slice(1)
}

const TARGETS = { campaign: 'Кампании', keyword: 'Ключевые фразы', query: 'Поисковые запросы' }
const TYPES = {
  cpa_above: 'CPA выше',
  spend_no_conv: 'Расход без конверсий от',
  cpc_above: 'CPC выше',
  daily_budget_pct: 'Израсходовано % дневного бюджета от',
}
const ACTIONS = {
  pause: 'Остановить',
  resume: 'Запустить',
  set_budget: 'Дневной бюджет',
  set_bid: 'Ставка',
  negative_adgroup: 'Минус-фраза группы',
  negative_campaign: 'Минус-фраза кампании',
  notify: 'Уведомить',
}

const { workspaces } = useWorkspaces()
const rules = ref([])
const runs = ref([])
const allAccounts = ref([])
const allGoals = ref([])
const lastRun = ref(null)
const busy = ref(false)
const form = reactive({
  name: '',
  target: 'campaign',
  rule_type: 'cpa_above',
  threshold_value: 1000,
  lookback_days: 7,
  dry_run: true,
  scope_account_id: '',
  scope_workspace_id: '',
  goal_row_id: '',
  action: 'pause',
  action_budget_rub: 500,
  action_bid_rub: 15,
})

const accounts = computed(() =>
  props.workspace ? (props.workspace.accounts || []).filter((a) => a.enabled) : allAccounts.value,
)
const goals = computed(() =>
  props.workspace
    ? (props.workspace.goals || []).map((g) => ({ id: g.row_id, goal_id: g.goal_id, name: g.name }))
    : allGoals.value,
)

watch(
  () => form.target,
  (target) => {
    if (target === 'keyword') {
      if (form.rule_type === 'daily_budget_pct') form.rule_type = 'cpa_above'
      if (['set_budget', 'negative_adgroup', 'negative_campaign'].includes(form.action)) form.action = 'pause'
      form.goal_row_id = ''
    } else if (target === 'query') {
      if (form.rule_type === 'daily_budget_pct') form.rule_type = 'spend_no_conv'
      form.action = 'negative_adgroup'
      form.goal_row_id = ''
      if (!form.threshold_value) form.threshold_value = 300
    } else if (['set_bid', 'negative_adgroup', 'negative_campaign'].includes(form.action)) {
      form.action = 'pause'
    }
  },
)

function ruleName(id) {
  return rules.value.find((r) => r.id === id)?.name || `Правило #${id}`
}

async function load() {
  try {
    if (props.workspace) {
      const [r, rr] = await Promise.all([wsApi.rules(props.workspace.id), webApi.ruleRuns()])
      rules.value = r.items || []
      const ids = new Set(rules.value.map((x) => x.id))
      runs.value = (rr.items || []).filter((x) => ids.has(x.rule_id))
    } else {
      const [r, rr, me, g] = await Promise.all([webApi.rules(), webApi.ruleRuns(), webApi.me(), webApi.goals()])
      rules.value = r.items || []
      runs.value = rr.items || []
      allAccounts.value = (me.direct || []).filter((a) => a.enabled)
      allGoals.value = g.items || []
    }
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}

async function create() {
  busy.value = true
  try {
    await webApi.createRule({
      name: form.name,
      target: form.target,
      rule_type: form.rule_type,
      threshold_value: form.threshold_value,
      lookback_days: form.lookback_days,
      dry_run: form.dry_run,
      action: form.action,
      scope_account_id: form.scope_account_id ? Number(form.scope_account_id) : null,
      scope_workspace_id: props.workspace
        ? props.workspace.id
        : form.scope_workspace_id
          ? Number(form.scope_workspace_id)
          : null,
      goal_row_id: form.target === 'campaign' && form.goal_row_id ? Number(form.goal_row_id) : null,
      action_budget_rub: form.action === 'set_budget' ? Number(form.action_budget_rub) : null,
      action_bid_rub: form.action === 'set_bid' ? Number(form.action_bid_rub) : null,
    })
    pushToast('Правило создано')
    form.name = ''
    form.scope_account_id = ''
    form.goal_row_id = ''
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

async function run(rule, dry) {
  if (!dry) {
    const entity = { keyword: 'ключевым фразам', query: 'запросам', campaign: 'кампаниям' }[rule.target] || 'объектам'
    if (!await askConfirm(`Применить действие к совпавшим ${entity} в Директе? Это изменит реальные объекты.`)) return
  }
  busy.value = true
  try {
    lastRun.value = await webApi.runRule(rule.id, dry)
    pushToast(`Совпало: ${lastRun.value.matched}`)
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

async function patch(rule, payload) {
  busy.value = true
  try {
    await webApi.patchRule(rule.id, payload)
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

async function remove(rule) {
  if (!await askConfirm(`Удалить правило «${rule.name}»?`)) return
  busy.value = true
  try {
    await webApi.deleteRule(rule.id)
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>
