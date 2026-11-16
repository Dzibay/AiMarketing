<template>
  <div class="adm-dash" :class="{ 'is-loading': loading && data }">
    <p v-if="error && !data" class="panel empty">{{ error }}</p>
    <div v-else-if="!data" class="panel"><p class="empty">Загружаем…</p></div>

    <template v-else>
      <section class="panel">
        <div class="panel__head">
          <h2>По тарифам</h2>
        </div>
        <p class="hint">
          Человек — в своём тарифе на сегодня; закончившийся — «без тарифа». Выручка — оплаты за период за вычетом
          возвратов, расход — вызовы ИИ за период. Лендинг без пользователя сюда не входит.
        </p>
        <p v-if="!data.by_plan.length" class="empty">За период ни расходов, ни оплат.</p>
        <div v-else v-scroll-x class="table-scroll">
          <table class="table adm-costs__table">
            <thead>
              <tr>
                <th>Тариф</th>
                <th class="num">Людей</th>
                <th class="num">Выручка</th>
                <th class="num">Расход ИИ</th>
                <th class="num">Маржа</th>
                <th class="num">Маржа, %</th>
                <th class="num">Расход на одного</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="g in data.by_plan" :key="g.key" :class="{ 'adm-costs__sep': g.key === 'trial' }">
                <td>{{ g.name }}</td>
                <td class="num">{{ num(g.users) }}</td>
                <td class="num">{{ rubles(g.revenue_rub) }}</td>
                <td class="num">{{ rubAi(g.cost_rub) }}</td>
                <td class="num" :class="{ 'tone-bad': g.margin_rub < 0 }">{{ rubles(g.margin_rub) }}</td>
                <td class="num">{{ pct(g.margin_pct) }}</td>
                <td class="num">{{ rubAi(g.per_user_rub) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="panel">
        <div class="panel__head">
          <h2>По людям</h2>
          <label class="adm-costs__check">
            <input type="checkbox" :checked="loss" @change="setLoss($event.target.checked)" />
            Только убыточные<span class="cell-note"> · {{ num(data.losses) }}</span>
          </label>
          <span class="adm-charts__total">{{ num(data.found) }} {{ plural(data.found, 'человек', 'человека', 'человек') }}</span>
        </div>
        <p v-if="!data.items.length" class="empty">{{ loss ? 'Убыточных за период нет.' : 'Никого.' }}</p>
        <div v-else v-scroll-x class="table-scroll">
          <table class="table adm-costs__table adm-costs__table--click">
            <thead>
              <tr>
                <th v-for="c in COLUMNS" :key="c.key" :class="{ num: c.num }" :aria-sort="sortAria(c.key)">
                  <button type="button" class="adm-costs__sort" @click="sortBy(c.key)">
                    {{ c.label }}<span aria-hidden="true">{{ sort === c.key ? (desc ? ' ↓' : ' ↑') : '' }}</span>
                  </button>
                </th>
                <th>Тариф</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in data.items" :key="r.user_id" :class="{ 'is-loss': r.loss }" @click="openUser(r.user_id)">
                <td>
                  <RouterLink :to="userLink(r.user_id)" @click.stop>{{ r.email || `#${r.user_id}` }}</RouterLink>
                  <small v-if="r.name" class="cell-note">{{ r.name }}</small>
                </td>
                <td class="num">{{ num(r.calls) }}</td>
                <td class="num">{{ rubAi(r.cost_rub) }}</td>
                <td class="num">{{ rubles(r.revenue_rub) }}</td>
                <td class="num" :class="{ 'tone-bad': r.margin_rub < 0 }">{{ rubles(r.margin_rub, r.margin_rub > -10 && r.margin_rub < 10 ? 2 : 0) }}</td>
                <td class="num">{{ num(r.units, 2) }}</td>
                <td class="num" v-tip="r.limit != null ? `${num(r.limit_spent, 2)} из ${num(r.limit)} ед. в текущем периоде` : 'без лимита'">
                  {{ r.share != null ? pct(r.share, 0) : '—' }}
                </td>
                <td>{{ r.plan_name }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <nav v-if="data.pages > 1" class="adm-costs__pages" aria-label="Страницы">
          <button class="btn btn--ghost btn--sm" type="button" :disabled="data.page <= 1" @click="page = data.page - 1">← Назад</button>
          <span>Страница {{ data.page }} из {{ data.pages }}</span>
          <button class="btn btn--ghost btn--sm" type="button" :disabled="data.page >= data.pages" @click="page = data.page + 1">Дальше →</button>
        </nav>
      </section>
    </template>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { ADMIN_BASE } from './adminNav'
import { useCostsTab } from './aiCosts'
import { num, pct, plural, rubAi, rubles } from '../../utils/format'

/** Люди и тарифы: маржа по тарифам и по людям; убыточные (расход больше оплат) — подсвечены. */
const props = defineProps({ params: { type: Object, required: true } })
const emit = defineEmits(['period'])
const router = useRouter()

const COLUMNS = [
  { key: 'email', label: 'Пользователь' },
  { key: 'calls', label: 'Вызовов', num: true },
  { key: 'cost', label: 'Расход', num: true },
  { key: 'revenue', label: 'Выручка', num: true },
  { key: 'margin', label: 'Маржа', num: true },
  { key: 'units', label: 'Единиц', num: true },
  { key: 'share', label: 'Лимит', num: true },
]
const sort = ref('cost')
const desc = ref(true)
const page = ref(1)
const loss = ref(false)

const params = computed(() => props.params)
watch(params, () => (page.value = 1), { deep: true })

const { data, loading, error } = useCostsTab(
  'people',
  params,
  () => ({ sort: sort.value, desc: desc.value, page: page.value, loss: loss.value }),
  (p) => emit('period', p),
)

function sortBy(key) {
  if (sort.value === key) desc.value = !desc.value
  else {
    sort.value = key
    desc.value = key !== 'email'
  }
  page.value = 1
}
const sortAria = (key) => (sort.value === key ? (desc.value ? 'descending' : 'ascending') : undefined)

function setLoss(value) {
  loss.value = value
  page.value = 1
}

const userLink = (id) => ({ path: `${ADMIN_BASE}/users`, query: { user: id } })
const openUser = (id) => router.push(userLink(id))
</script>
