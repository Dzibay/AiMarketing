<template>
  <div class="site">
    <SiteHeader solid />
    <main class="cabinet">
      <div class="wrap">
        <div class="cabinet__head">
          <div>
            <h1>Правила</h1>
            <p>Автоматизация кампаний, ключей и поисковых запросов: сначала dry-run.</p>
          </div>
        </div>
        <CabinetNav />

        <section class="panel">
          <h2>Новое правило</h2>
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
              <label>Тип</label>
              <select v-model="form.rule_type">
                <option value="cpa_above">CPA выше порога (₽)</option>
                <option value="spend_no_conv">Расход без конверсий (₽)</option>
                <option value="cpc_above">CPC выше порога (₽)</option>
                <option v-if="form.target === 'campaign'" value="daily_budget_pct">
                  % дневного бюджета
                </option>
              </select>
            </div>
            <div class="field">
              <label>Порог</label>
              <input v-model.number="form.threshold_value" type="number" min="0" step="1" />
            </div>
            <div v-if="form.target === 'campaign'" class="field">
              <label>Дней</label>
              <input v-model.number="form.lookback_days" type="number" min="1" max="30" />
            </div>
            <div class="field">
              <label>Аккаунт</label>
              <select v-model="form.scope_account_id">
                <option value="">Все</option>
                <option v-for="a in accounts" :key="a.id" :value="String(a.id)">
                  {{ a.name || a.client_login }}
                </option>
              </select>
            </div>
            <div v-if="form.target === 'campaign'" class="field">
              <label>Цель Метрики</label>
              <select v-model="form.goal_row_id">
                <option value="">Конверсии Директа</option>
                <option v-for="g in goals" :key="g.id" :value="String(g.id)">
                  {{ g.name || `Цель ${g.goal_id}` }} · {{ g.counter_name }}
                </option>
              </select>
            </div>
            <div class="field">
              <label>Действие</label>
              <select v-model="form.action">
                <option v-if="form.target !== 'query'" value="pause">Пауза</option>
                <option v-if="form.target !== 'query'" value="resume">Запуск</option>
                <option v-if="form.target === 'campaign'" value="set_budget">Дневной бюджет</option>
                <option v-if="form.target === 'keyword'" value="set_bid">Ставка ключа</option>
                <option v-if="form.target === 'query'" value="negative_adgroup">В минуса группы</option>
                <option v-if="form.target === 'query'" value="negative_campaign">В минуса кампании</option>
                <option value="notify">Только уведомить</option>
              </select>
            </div>
            <div v-if="form.action === 'set_budget'" class="field">
              <label>Бюджет ₽</label>
              <input v-model.number="form.action_budget_rub" type="number" min="1" step="1" />
            </div>
            <div v-if="form.action === 'set_bid'" class="field">
              <label>Ставка ₽</label>
              <input v-model.number="form.action_bid_rub" type="number" min="0.01" step="0.01" />
            </div>
            <label class="check-row">
              <input v-model="form.dry_run" type="checkbox" />
              Dry-run
            </label>
            <button class="btn btn--accent" type="button" @click="create">Создать</button>
          </div>
          <p v-if="form.target === 'keyword'" class="hint">
            Для ключей используются period-метрики после «Синк статистики» на странице кампании.
          </p>
          <p v-if="form.target === 'query'" class="hint">
            Для запросов нужен «Синк статистики» на странице кампании. Типичный сценарий: расход без
            конверсий → минус-фраза.
          </p>
        </section>

        <section class="panel">
          <h2>Список</h2>
          <div v-if="rules.length" class="list">
            <div v-for="rule in rules" :key="rule.id" class="list-row">
              <div>
                <strong>{{ rule.name }}</strong>
                <small>
                  {{ targetLabel(rule.target) }} · {{ rule.rule_type }} ·
                  {{ actionLabel(rule.action) }} · порог {{ rule.threshold_value }}
                  <template v-if="rule.goal_name"> · цель «{{ rule.goal_name }}»</template>
                  <template v-if="rule.action === 'set_budget' && rule.action_budget_rub">
                    · бюджет {{ rule.action_budget_rub }} ₽
                  </template>
                  <template v-if="rule.action === 'set_bid' && rule.action_bid_rub">
                    · ставка {{ rule.action_bid_rub }} ₽
                  </template>
                  · {{ rule.dry_run ? 'dry-run' : 'auto' }} · {{ rule.enabled ? 'вкл' : 'выкл' }}
                </small>
              </div>
              <div class="row-actions">
                <button class="btn btn--ghost" type="button" @click="run(rule, true)">Проверка</button>
                <button class="btn btn--ghost" type="button" @click="run(rule, false)">Применить</button>
                <button class="btn btn--ghost" type="button" @click="toggle(rule)">
                  {{ rule.enabled ? 'Выкл' : 'Вкл' }}
                </button>
                <button class="btn btn--ghost" type="button" @click="toggleDry(rule)">
                  {{ rule.dry_run ? 'В авто' : 'В dry-run' }}
                </button>
                <button class="btn btn--ghost" type="button" @click="remove(rule.id)">Удалить</button>
              </div>
            </div>
          </div>
          <p v-else class="empty">Правил пока нет.</p>
        </section>

        <section v-if="lastRun" class="panel">
          <h2>Последний прогон</h2>
          <p class="hint">
            Объект: {{ targetLabel(lastRun.target) }}, совпало: {{ lastRun.matched }},
            применено: {{ lastRun.applied }}, действие: {{ actionLabel(lastRun.action) }},
            режим: {{ lastRun.dry_run ? 'dry-run' : 'auto' }}
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
        </section>

        <section v-if="runs.length" class="panel">
          <h2>История</h2>
          <div class="list">
            <div v-for="runItem in runs" :key="runItem.id" class="list-row">
              <div>
                <strong>Правило #{{ runItem.rule_id }}</strong>
                <small>
                  matched {{ runItem.matched }} / applied {{ runItem.applied }} ·
                  {{ runItem.dry_run ? 'dry-run' : 'auto' }} · {{ runItem.created_at }}
                </small>
              </div>
            </div>
          </div>
        </section>
      </div>
    </main>
    <SiteFooter />
  </div>
</template>

<script setup>
import { onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import CabinetNav from '../components/CabinetNav.vue'
import SiteFooter from '../components/SiteFooter.vue'
import SiteHeader from '../components/SiteHeader.vue'
import { errorText, webApi } from '../api/web'
import { getSession } from '../composables/useSession'
import { pushToast } from '../composables/useToasts'
import { useHead } from '../utils/useHead'

useHead('Правила — AiMarketing', true)

const router = useRouter()
const rules = ref([])
const runs = ref([])
const accounts = ref([])
const goals = ref([])
const lastRun = ref(null)
const form = reactive({
  name: '',
  target: 'campaign',
  rule_type: 'cpa_above',
  threshold_value: 1000,
  lookback_days: 7,
  dry_run: true,
  scope_account_id: '',
  goal_row_id: '',
  action: 'pause',
  action_budget_rub: 500,
  action_bid_rub: 15,
})

watch(
  () => form.target,
  (target) => {
    if (target === 'keyword') {
      if (form.rule_type === 'daily_budget_pct') form.rule_type = 'cpa_above'
      if (['set_budget', 'negative_adgroup', 'negative_campaign'].includes(form.action)) {
        form.action = 'pause'
      }
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

function targetLabel(target) {
  return { keyword: 'ключи', query: 'запросы', campaign: 'кампании' }[target] || 'кампании'
}

function actionLabel(action) {
  return (
    {
      pause: 'пауза',
      resume: 'запуск',
      set_budget: 'бюджет',
      set_bid: 'ставка',
      negative_adgroup: 'минус группы',
      negative_campaign: 'минус кампании',
      notify: 'уведомление',
    }[action] || action || 'пауза'
  )
}

async function load() {
  if (!getSession()) {
    router.replace('/login')
    return
  }
  try {
    const [r, rr, me, g] = await Promise.all([
      webApi.rules(),
      webApi.ruleRuns(),
      webApi.me(),
      webApi.goals(),
    ])
    rules.value = r.items || []
    runs.value = rr.items || []
    accounts.value = (me.direct || []).filter((a) => a.enabled)
    goals.value = g.items || []
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}

async function create() {
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
      goal_row_id:
        form.target === 'campaign' && form.goal_row_id ? Number(form.goal_row_id) : null,
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
  }
}

async function run(rule, dry) {
  if (!dry) {
    const entity =
      { keyword: 'ключам', query: 'запросам', campaign: 'кампаниям' }[rule.target] || 'объектам'
    const ok = window.confirm(
      `Применить действие к совпавшим ${entity} в Директе? Это изменит реальные объекты.`,
    )
    if (!ok) return
  }
  try {
    lastRun.value = await webApi.runRule(rule.id, dry)
    pushToast(`Совпало: ${lastRun.value.matched}`)
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}

async function toggle(rule) {
  try {
    await webApi.patchRule(rule.id, { enabled: !rule.enabled })
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}

async function toggleDry(rule) {
  try {
    await webApi.patchRule(rule.id, { dry_run: !rule.dry_run })
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}

async function remove(id) {
  try {
    await webApi.deleteRule(id)
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}

onMounted(load)
</script>
