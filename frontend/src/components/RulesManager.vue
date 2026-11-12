<template>
  <div class="rules-manager">
    <FeatureLockNotice feature="autopilot">
      Автопилот не входит в ваш тариф: новые правила не создаются, а действующие не проверяются. Выключить или удалить
      правило можно и сейчас.
    </FeatureLockNotice>

    <div class="ws-toolbar">
      <p class="sync-status">
        Проверяются каждый час, срабатывают не чаще раза в сутки. Работают без ИИ — лимит не тратится.
      </p>
      <button v-if="!adding" class="btn btn--accent" type="button" @click="adding = true">
        <Plus :size="15" aria-hidden="true" />Новое правило
      </button>
    </div>

    <section v-if="adding" class="ccard builder">
      <h3>Новое правило</h3>
      <div class="sentence">
        <span>Если у</span>
        <select v-model="form.target" aria-label="Что проверять">
          <option value="campaign">кампании</option>
          <option value="keyword">фразы</option>
          <option value="query">запроса</option>
        </select>
        <select v-model="form.rule_type" aria-label="Условие">
          <option value="cpa_above">{{ COND.cpa_above }}</option>
          <option v-if="form.target !== 'query'" value="cpa_below">{{ COND.cpa_below }}</option>
          <option value="spend_no_conv">{{ COND.spend_no_conv }}</option>
          <option value="cpc_above">{{ COND.cpc_above }}</option>
          <option v-if="form.target === 'campaign'" value="daily_budget_pct">{{ COND.daily_budget_pct }}</option>
          <option v-if="form.target === 'campaign'" value="weekly_budget_pct">{{ COND.weekly_budget_pct }}</option>
        </select>
        <span class="inp">
          <input v-model.number="form.threshold_value" type="number" min="0" step="1" aria-label="Порог" />
          <i>{{ unitOf(form.rule_type) }}</i>
        </span>
        <template v-if="form.target === 'campaign'">
          <span>за</span>
          <span class="inp">
            <input v-model.number="form.lookback_days" type="number" min="1" max="30" aria-label="За сколько дней" />
            <i>дн.</i>
          </span>
        </template>
        <span>—</span>
        <select v-model="form.action" aria-label="Что сделать">
          <option v-if="form.target !== 'query'" value="pause">{{ DO.pause }}</option>
          <option v-if="form.target !== 'query'" value="resume">{{ DO.resume }}</option>
          <option v-if="form.target === 'campaign'" value="set_budget">{{ DO.set_budget }}</option>
          <option v-if="form.target === 'campaign'" value="set_weekly_budget">{{ DO.set_weekly_budget }}</option>
          <option v-if="form.target === 'campaign'" value="change_budget_pct">{{ DO.change_budget_pct }}</option>
          <option v-if="form.target === 'keyword'" value="set_bid">{{ DO.set_bid }}</option>
          <option v-if="form.target === 'query'" value="negative_adgroup">{{ DO.negative_adgroup }}</option>
          <option v-if="form.target === 'query'" value="negative_campaign">{{ DO.negative_campaign }}</option>
          <option value="notify">{{ DO.notify }}</option>
        </select>
        <span v-if="['set_budget', 'set_weekly_budget'].includes(form.action)" class="inp">
          <input v-model.number="form.action_budget_rub" type="number" min="300" step="100" aria-label="Бюджет" />
          <i>{{ form.action === 'set_weekly_budget' ? '₽/нед.' : '₽' }}</i>
        </span>
        <template v-if="form.action === 'change_budget_pct'">
          <span>на</span>
          <span class="inp">
            <input v-model.number="form.action_pct" type="number" min="-90" max="500" step="1" aria-label="Изменить на, %" />
            <i>%</i>
          </span>
        </template>
        <span v-if="form.action === 'set_bid'" class="inp">
          <input v-model.number="form.action_bid_rub" type="number" min="0.3" step="0.1" aria-label="Ставка" />
          <i>₽</i>
        </span>
      </div>

      <div class="builder__fields">
        <div v-field class="field">
          <label>Название</label>
          <input v-model="form.name" type="text" placeholder="Например: дорогие фразы" />
        </div>
        <div v-if="!workspace" v-field class="field">
          <label>Где работает</label>
          <select v-model="form.scope_workspace_id">
            <option value="">Все сайты</option>
            <option v-for="w in workspaces" :key="w.id" :value="String(w.id)">{{ w.name }}</option>
          </select>
        </div>
        <div v-field class="field">
          <label>Кабинет Директа</label>
          <select v-model="form.scope_account_id">
            <option value="">Все</option>
            <option v-for="a in accounts" :key="a.id" :value="String(a.id)">{{ a.name || a.client_login }}</option>
          </select>
        </div>
        <div v-field class="field">
          <label>Кампания</label>
          <select v-model="form.scope_campaign_row_id">
            <option value="">Все</option>
            <option v-for="c in campaignOptions" :key="c.id" :value="String(c.id)">
              {{ campaignLabel(c, repeatedCampaigns) }}
            </option>
          </select>
        </div>
        <div v-if="form.target === 'campaign'" v-field class="field">
          <label>Конверсии</label>
          <select v-model="form.goal_row_id">
            <option value="">Конверсии Директа</option>
            <option v-for="g in goals" :key="g.id" :value="String(g.id)">
              {{ g.name || `Цель ${g.goal_id}` }}<template v-if="g.counter_name"> · {{ g.counter_name }}</template>
            </option>
          </select>
        </div>
        <div v-if="form.action === 'change_budget_pct'" v-field class="field">
          <label>{{ form.action_pct < 0 ? 'Не ниже' : 'Не выше' }}, ₽/нед. (необязательно)</label>
          <input v-model.number="form.action_cap_rub" type="number" min="300" step="100" />
        </div>
      </div>

      <label class="builder__dry">
        <span class="switch"><input v-model="form.dry_run" type="checkbox" /></span>
        <span>
          <b>Сначала только проверять</b>
          <small>Правило пишет, что сделало бы, но рекламу не трогает. Удобно в первую неделю.</small>
        </span>
      </label>
      <p v-if="['set_weekly_budget', 'change_budget_pct'].includes(form.action)" class="hint">
        Пример: CPA ниже 800 ₽ за 7 дней → бюджет +20 %, не выше 30 000 ₽/нед. Автостратегии и ручные ставки ЕПК
        получат недельный бюджет, ручные ставки ТГО — дневной (недельная сумма ÷ 7). Каждый запуск — запись в «Истории»
        с откатом.
      </p>
      <p v-if="form.target !== 'campaign'" class="hint">
        Для фраз и запросов нужна статистика структуры: кампания → «Обновить структуру и статистику».
        <template v-if="form.target === 'query'">Типичный сценарий: расход без конверсий → минус-фраза.</template>
      </p>
      <div class="builder__foot">
        <button class="btn btn--accent" type="button" :disabled="busy" @click="create">Сохранить правило</button>
        <button class="btn btn--ghost" type="button" @click="adding = false">Отмена</button>
      </div>
    </section>

    <h2 class="section-h rules-h">
      Правила <span class="cgroup__n">{{ rules.length }}</span>
      <small v-if="workspace"><RouterLink to="/cabinet/autopilot">Общие правила для всех сайтов</RouterLink></small>
      <small v-else>Эксперименты с объявлениями — в автопилоте проекта</small>
    </h2>
    <div v-if="rules.length" class="ccard rules-list">
      <div v-for="rule in rules" :key="rule.id" class="prow rule" :class="{ 'is-off': !rule.enabled }">
        <label class="switch" :title="rule.enabled ? 'Выключить правило' : 'Включить правило'">
          <input
            type="checkbox"
            :checked="rule.enabled"
            :disabled="busy"
            :aria-label="`Правило «${rule.name}»`"
            @change="patch(rule, { enabled: !rule.enabled })"
          />
        </label>
        <div class="prow__main">
          <b>
            {{ rule.name }}
            <UiStatus
              v-if="rule.dry_run"
              tone="info"
              :icon="Eye"
              label="Только проверка"
              hint="Пишет, что сделало бы, но в Директе ничего не меняет"
            />
          </b>
          <p>{{ ruleText(rule) }}</p>
          <small>{{ ruleMeta(rule) }}</small>
        </div>
        <div class="prow__acts">
          <button
            class="btn btn--ghost btn--sm"
            type="button"
            :disabled="busy"
            title="Посчитать по текущей статистике, ничего не меняя"
            @click="run(rule, true)"
          >
            Проверить
          </button>
          <UiMenu size="sm" :label="`Действия с правилом «${rule.name}»`">
            <UiMenuItem :icon="Play" :disabled="busy" @click="run(rule, false)">Применить сейчас</UiMenuItem>
            <UiMenuItem :icon="rule.dry_run ? Zap : Eye" :disabled="busy" @click="patch(rule, { dry_run: !rule.dry_run })">
              {{ rule.dry_run ? 'Разрешить менять рекламу' : 'Перевести в «только проверку»' }}
            </UiMenuItem>
            <UiMenuItem :icon="Trash2" danger :disabled="busy" @click="remove(rule)">Удалить правило</UiMenuItem>
          </UiMenu>
        </div>
      </div>
    </div>
    <EmptyState
      v-else
      :icon="Zap"
      :text="workspace ? 'Для этого сайта правил пока нет.' : 'Правил пока нет.'"
    >
      <template v-if="!adding" #action>
        <button class="btn" type="button" @click="adding = true">Создать правило</button>
      </template>
    </EmptyState>

    <section v-if="lastRun" class="ccard run-result">
      <header>
        <div>
          <b>
            {{ lastRun.dry_run ? 'Проверка' : 'Применение' }}<template v-if="lastRunName"> «{{ lastRunName }}»</template>:
            {{ runSummary(lastRun) }}
          </b>
          <small v-if="lastRun.dry_run">В Директе ничего не изменено.</small>
        </div>
        <button class="btn btn--ghost" type="button" @click="lastRun = null">Скрыть</button>
      </header>
      <p v-if="lastRun.error" class="form-error">Применилось не всё: {{ lastRun.error }}</p>
      <div v-for="item in lastRun.items || []" :key="`${item.entity}-${item.row_id}`" class="prow">
        <div class="prow__main">
          <b>{{ item.name }}</b>
          <p>
            {{ item.reason }}
            <template v-if="item.campaign_name"> · {{ item.campaign_name }}</template>
            <template v-if="item.adgroup_name"> · {{ item.adgroup_name }}</template>
          </p>
        </div>
      </div>
    </section>

    <h2 class="section-h rules-h">
      Последние запуски
      <small><RouterLink :to="historyTo">Что изменили и как вернуть — в «Истории»</RouterLink></small>
    </h2>
    <div v-if="runs.length" class="ccard table-scroll">
      <table class="table">
        <thead>
          <tr>
            <th>Когда</th>
            <th>Правило</th>
            <th>Что сделало</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in runs.slice(0, 20)" :key="item.id">
            <td class="rules-when">{{ formatDateTime(item.created_at) }}</td>
            <td><b>{{ ruleName(item.rule_id) }}</b></td>
            <td>
              {{ runSummary(item) }}
              <small v-if="item.error" class="form-error">Применилось не всё: {{ item.error }}</small>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-else class="ccard ccard--note">Запусков ещё не было.</p>
  </div>
</template>

<script setup>
import { askConfirm } from '../composables/useConfirm'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { Eye, Play, Plus, Trash2, Zap } from '@lucide/vue'
import FeatureLockNotice from './FeatureLockNotice.vue'
import EmptyState from './ui/EmptyState.vue'
import UiMenu from './ui/UiMenu.vue'
import UiMenuItem from './ui/UiMenuItem.vue'
import UiStatus from './ui/UiStatus.vue'
import { errorText, webApi, wsApi } from '../api/web'
import { pushToast } from '../composables/useToasts'
import { useWorkspaces } from '../composables/useWorkspaces'
import { formatDateTime, num, plural, rubles } from '../utils/format'
import { campaignLabel, repeatedNames } from '../utils/campaignNames'

/**
 * Правила Автопилота: условие читается как предложение («Если у кампании CPA выше 1 000 ₽ за 7 дней — остановить»),
 * список с переключателем и меню действий, последние запуски.
 */
const props = defineProps({
  // Проект: правила только его кампаний; без него — все правила пользователя.
  workspace: { type: Object, default: null },
})

// Падежи под «Если у …».
const WHO = { campaign: 'кампании', keyword: 'фразы', query: 'запроса' }
const COND = {
  cpa_above: 'CPA выше',
  cpa_below: 'CPA ниже',
  spend_no_conv: 'расход без конверсий больше',
  cpc_above: 'CPC выше',
  daily_budget_pct: 'израсходовано дневного бюджета больше',
  weekly_budget_pct: 'израсходовано недельного бюджета больше',
}
const DO = {
  pause: 'остановить',
  resume: 'запустить',
  set_budget: 'поставить дневной бюджет',
  set_weekly_budget: 'поставить недельный бюджет',
  change_budget_pct: 'изменить бюджет',
  set_bid: 'поставить ставку',
  negative_adgroup: 'добавить в минус-фразы группы',
  negative_campaign: 'добавить в минус-фразы кампании',
  notify: 'только уведомить',
}

function unitOf(type) {
  return String(type).endsWith('_pct') ? '%' : '₽'
}

function days(n) {
  return `${n} ${plural(n, 'день', 'дня', 'дней')}`
}

function ruleText(r) {
  const th = unitOf(r.rule_type) === '%' ? `${num(r.threshold_value)} %` : rubles(r.threshold_value)
  let text = `Если у ${WHO[r.target] || WHO.campaign} ${COND[r.rule_type] || r.rule_type} ${th}`
  if ((r.target || 'campaign') === 'campaign') text += ` за ${days(r.lookback_days)}`
  text += ` — ${DO[r.action] || r.action}`
  if (r.action === 'set_budget' && r.action_budget_rub) text += ` ${rubles(r.action_budget_rub)}`
  if (r.action === 'set_weekly_budget' && r.action_budget_rub) text += ` ${rubles(r.action_budget_rub)} в неделю`
  if (r.action === 'change_budget_pct' && r.action_pct != null) {
    text += ` на ${r.action_pct > 0 ? '+' : ''}${num(r.action_pct)} %`
    if (r.action_budget_rub) {
      text += `, ${r.action_pct < 0 ? 'не ниже' : 'не выше'} ${rubles(r.action_budget_rub)} в неделю`
    }
  }
  if (r.action === 'set_bid' && r.action_bid_rub) text += ` ${rubles(r.action_bid_rub, 2)}`
  return text
}

/** Где работает правило и когда запускалось — строка под условием. */
function ruleMeta(r) {
  const parts = []
  if (!props.workspace) parts.push(r.workspace_name || 'Все сайты')
  if (r.account_name) parts.push(r.account_name)
  if (r.campaign_name) parts.push(`кампания «${r.campaign_name}»`)
  if (r.goal_name) parts.push(`цель «${r.goal_name}»`)
  if (!r.enabled) parts.push('выключено, ничего не проверяет')
  else if (r.last_run_at) parts.push(`последний запуск ${formatDateTime(r.last_run_at)}`)
  else parts.push('первый запуск — в течение часа')
  return parts.join(' · ')
}

/** Итог запуска: проверка только считает, применение — меняет Директ. */
function runSummary(run) {
  const n = run.matched || 0
  if (!n) return 'под условие ничего не попало'
  const found = `нашло ${n}`
  if (run.dry_run) return `${found}, ничего не меняло`
  return `${found}, изменено ${run.applied || 0}`
}

const CAMPAIGN_TYPES = ['daily_budget_pct', 'weekly_budget_pct']
const CAMPAIGN_ACTIONS = ['set_budget', 'set_weekly_budget', 'change_budget_pct']

const { workspaces } = useWorkspaces()
const rules = ref([])
const runs = ref([])
const allAccounts = ref([])
const allGoals = ref([])
const allCampaigns = ref([])
const lastRun = ref(null)
const busy = ref(false)
const adding = ref(false)
const lastRunId = ref(null)
const form = reactive({
  name: '',
  target: 'campaign',
  rule_type: 'cpa_above',
  threshold_value: 1000,
  lookback_days: 7,
  dry_run: true,
  scope_account_id: '',
  scope_workspace_id: '',
  scope_campaign_row_id: '',
  goal_row_id: '',
  action: 'pause',
  action_budget_rub: 500,
  action_pct: 20,
  action_cap_rub: null,
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

// Кампании выбранных кабинета и проекта; выбор вне них сбрасываем.
const campaignOptions = computed(() => {
  const ws = props.workspace ? '' : form.scope_workspace_id
  return allCampaigns.value.filter(
    (c) =>
      (!form.scope_account_id || String(c.account_id) === form.scope_account_id) &&
      (!ws || (c.workspace_ids || []).map(String).includes(ws)),
  )
})
const repeatedCampaigns = computed(() => repeatedNames(campaignOptions.value.map((c) => c.name)))

watch(campaignOptions, (list) => {
  if (form.scope_campaign_row_id && !list.some((c) => String(c.id) === form.scope_campaign_row_id)) {
    form.scope_campaign_row_id = ''
  }
})

watch(
  () => form.target,
  (target) => {
    if (target === 'keyword') {
      if (CAMPAIGN_TYPES.includes(form.rule_type)) form.rule_type = 'cpa_above'
      if ([...CAMPAIGN_ACTIONS, 'negative_adgroup', 'negative_campaign'].includes(form.action)) form.action = 'pause'
      form.goal_row_id = ''
    } else if (target === 'query') {
      if ([...CAMPAIGN_TYPES, 'cpa_below'].includes(form.rule_type)) form.rule_type = 'spend_no_conv'
      form.action = 'negative_adgroup'
      form.goal_row_id = ''
      if (!form.threshold_value) form.threshold_value = 300
    } else if (['set_bid', 'negative_adgroup', 'negative_campaign'].includes(form.action)) {
      form.action = 'pause'
    }
  },
)

const lastRunName = computed(() => (lastRunId.value ? ruleName(lastRunId.value) : ''))
const historyTo = computed(() => ({
  path: props.workspace ? `/w/${props.workspace.id}/history` : '/cabinet/history',
  query: { source: 'rule' },
}))

function ruleName(id) {
  return rules.value.find((r) => r.id === id)?.name || `Правило #${id}`
}

async function load() {
  try {
    if (props.workspace) {
      const [r, rr] = await Promise.all([wsApi.rules(props.workspace.id), webApi.ruleRuns()])
      rules.value = r.items || []
      allCampaigns.value = r.campaigns || []
      const ids = new Set(rules.value.map((x) => x.id))
      runs.value = (rr.items || []).filter((x) => ids.has(x.rule_id))
    } else {
      const [r, rr, me, g] = await Promise.all([webApi.rules(), webApi.ruleRuns(), webApi.me(), webApi.goals()])
      rules.value = r.items || []
      allCampaigns.value = r.campaigns || []
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
      scope_campaign_row_id: form.scope_campaign_row_id ? Number(form.scope_campaign_row_id) : null,
      goal_row_id: form.target === 'campaign' && form.goal_row_id ? Number(form.goal_row_id) : null,
      action_budget_rub: ['set_budget', 'set_weekly_budget'].includes(form.action)
        ? Number(form.action_budget_rub)
        : form.action === 'change_budget_pct' && form.action_cap_rub
          ? Number(form.action_cap_rub)
          : null,
      action_pct: form.action === 'change_budget_pct' ? Number(form.action_pct) : null,
      action_bid_rub: form.action === 'set_bid' ? Number(form.action_bid_rub) : null,
    })
    pushToast(form.dry_run ? 'Правило сохранено в режиме проверки' : 'Правило сохранено')
    adding.value = false
    form.name = ''
    form.scope_account_id = ''
    form.scope_campaign_row_id = ''
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
    lastRunId.value = rule.id
    pushToast(
      lastRun.value.error ? `Применилось не всё: ${lastRun.value.error}` : `Правило ${runSummary(lastRun.value)}`,
      lastRun.value.error ? 'error' : undefined,
    )
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

<style scoped>
.rules-manager .ws-toolbar .btn {
  gap: 6px;
}
.builder {
  margin-bottom: 8px;
  padding: 18px;
  border-color: var(--faint);
}
.builder h3 {
  margin: 0 0 12px;
  font-size: 15px;
}
.sentence {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
  font-size: 15px;
  font-weight: 600;
}
.sentence select {
  /* По ширине выбранного варианта, а не самого длинного, — так предложение читается слитно. */
  field-sizing: content;
  height: 34px;
  padding: 0 10px;
  border: 1px solid var(--line);
  border-radius: 9px;
  background: var(--surface);
  color: var(--ink);
  font: inherit;
  font-size: 14px;
}
.inp {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 34px;
  padding: 0 10px;
  border: 1px solid var(--line);
  border-radius: 9px;
  background: var(--surface);
  font-size: 14px;
}
.inp:focus-within,
.sentence select:focus {
  border-color: var(--ink);
  outline: 0;
}
.inp input {
  width: 64px;
  padding: 0;
  border: 0;
  outline: 0;
  background: none;
  color: var(--ink);
  font: inherit;
  text-align: right;
}
.inp i {
  color: var(--faint);
  font-style: normal;
  font-size: 13px;
}
.builder__fields {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}
.builder__dry {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 12px 14px;
  border: 1px solid var(--line);
  border-radius: 12px;
  cursor: pointer;
}
.builder__dry .switch {
  margin-top: 2px;
}
.builder__dry small {
  display: block;
  color: var(--muted);
  font-size: 13px;
}
.builder .hint {
  margin: 12px 0 0;
}
.builder__foot {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 16px;
}

.rules-h {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px;
}
.rules-h small {
  margin-left: auto;
  color: var(--muted);
  font-size: 13px;
  font-weight: 500;
}
.rules-h small a {
  color: var(--muted);
}
.rule {
  align-items: flex-start;
  justify-content: flex-start;
}
.rule > .switch {
  margin-top: 3px;
}
.rule .prow__main {
  flex: 1;
}
.rule .prow__main b {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 8px;
}
.rule.is-off .prow__main b,
.rule.is-off .prow__main p {
  color: var(--faint);
}
.rule .prow__acts {
  gap: 6px;
}
.run-result {
  margin-top: 12px;
}
.run-result header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 18px;
  border-bottom: 1px solid var(--line);
}
.run-result header small {
  display: block;
  color: var(--muted);
  font-size: 13px;
}
.run-result .form-error {
  margin: 0;
  padding: 10px 18px;
}
.rules-when {
  white-space: nowrap;
  color: var(--muted);
}
.table small.form-error {
  display: block;
}
@media (max-width: 640px) {
  .rule {
    flex-wrap: wrap;
  }
  .rule .prow__main {
    flex-basis: calc(100% - 60px);
  }
  .rule .prow__acts {
    width: 100%;
    justify-content: flex-end;
  }
  .rules-h small {
    margin-left: 0;
    flex-basis: 100%;
  }
}
</style>
