<template>
  <section class="panel">
    <div class="panel__head">
      <h2>Расход сервиса</h2>
      <UiSelect
        v-if="data && data.months.length > 1"
        :model-value="data.month"
        :options="data.months.map((m) => ({ value: m, label: monthLabel(m) }))"
        :disabled="busy"
        aria-label="Месяц"
        @change="load"
      />
    </div>
    <p class="hint">
      Единицы — списано с лимитов пользователей. Себестоимость — сколько это стоило сервису по ценам, заданным в «ИИ и
      сервисах». Проверки подключения в админке сюда не попадают: они ничего не списывают.
    </p>
    <p v-if="!data" class="empty">Загружаем…</p>
    <template v-else>
      <div class="stats-grid">
        <div class="stat-card">
          <small>Вызовов</small>
          <strong>{{ num(data.total.calls) }}</strong>
        </div>
        <div class="stat-card">
          <small>Списано единиц</small>
          <strong>{{ num(data.total.units, 2) }}</strong>
        </div>
        <div class="stat-card">
          <small>Себестоимость</small>
          <strong>{{ rubAi(data.total.cost_rub) }}</strong>
        </div>
        <div class="stat-card" v-tip="'Доля оценок «палец вниз» среди всех оценок ответов за месяц, по классам моделей'">
          <small>Ответы «вниз»</small>
          <strong :class="downTone">{{ feedbackTotal ? pct(downShare) : '—' }}</strong>
          <span class="cell-note">
            <template v-if="!feedbackTotal">оценок нет</template>
            <template v-else>
              <span v-for="(m, i) in data.feedback" :key="m.model">{{ i ? ' · ' : '' }}{{ m.model || 'без модели' }}: {{ pct(m.down_share) }} из {{ num(m.total) }}</span>
            </template>
          </span>
        </div>
      </div>
      <p v-if="!data.total.calls" class="empty">В этом месяце платных вызовов не было.</p>
      <template v-else>
        <h3 class="subhead">По видам</h3>
        <div v-scroll-x class="table-scroll">
          <table class="table usage-table">
            <thead>
              <tr>
                <th>Вид</th>
                <th class="num">Вызовов</th>
                <th class="num" title="Вход (доля из кэша провайдера) / выход">Токенов</th>
                <th class="num">Единиц</th>
                <th class="num">₽</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in data.by_kind" :key="`${row.kind}:${row.model_class}`">
                <td>{{ row.label }}</td>
                <td class="num">{{ num(row.calls) }}</td>
                <td class="num">{{ row.kind === 'model' ? `${num(row.tokens_in)}${cacheShare(row)} / ${num(row.tokens_out)}` : '—' }}</td>
                <td class="num">{{ num(row.units, 2) }}</td>
                <td class="num">{{ rubAi(row.cost_rub) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <template v-if="data.by_model?.length">
          <h3 class="subhead">Какие модели отвечали</h3>
          <div v-scroll-x class="table-scroll">
            <table class="table usage-table">
              <thead>
                <tr>
                  <th>Модель</th>
                  <th>Класс (цена)</th>
                  <th class="num" title="Сколько из этих вызовов модель сделала вместо назначенной">Вызовов · подменой</th>
                  <th class="num">Единиц</th>
                  <th class="num">₽</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in data.by_model" :key="`${row.model}:${row.model_class}`">
                  <td>{{ row.model || '—' }}</td>
                  <td>{{ row.class_name || '—' }}</td>
                  <td class="num">{{ num(row.calls) }}<template v-if="row.substitutes"> · {{ num(row.substitutes) }}</template></td>
                  <td class="num">{{ num(row.units, 2) }}</td>
                  <td class="num">{{ rubAi(row.cost_rub) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </template>
        <template v-if="data.substitutions?.length">
          <h3 class="subhead">Подмены моделей</h3>
          <p class="hint">Назначенная модель не ответила — ответила запасная. Пользователь этого не видит.</p>
          <div v-scroll-x class="table-scroll">
            <table class="table usage-table">
              <thead>
                <tr>
                  <th>Когда</th>
                  <th>Задача</th>
                  <th>Назначена → ответила</th>
                  <th>Почему</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(row, i) in data.substitutions" :key="i">
                  <td>{{ formatDateTime(row.created_at) }}</td>
                  <td>{{ row.task }}<small v-if="row.class_name" class="cell-note">{{ row.class_name }}</small></td>
                  <td>{{ row.from }} → {{ row.model }}</td>
                  <td class="usage-reason">{{ row.reason }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </template>
        <h3 class="subhead">Больше всего расходуют</h3>
        <div v-scroll-x class="table-scroll">
          <table class="table usage-table">
            <thead>
              <tr>
                <th>Пользователь</th>
                <th class="num">Вызовов</th>
                <th class="num">Единиц</th>
                <th class="num">₽</th>
                <th class="num">Наценка</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in data.by_user" :key="row.user_id">
                <td>
                  <RouterLink :to="{ path: '/admin-panel/users', query: { user: row.user_id } }">
                    {{ row.email || `#${row.user_id}` }}
                  </RouterLink>
                  <small v-if="row.name" class="cell-note">{{ row.name }}</small>
                </td>
                <td class="num">{{ num(row.calls) }}</td>
                <td class="num">{{ num(row.units, 2) }}</td>
                <td class="num">{{ rubAi(row.cost_rub) }}</td>
                <td class="num">{{ row.cost_rub > 0 ? `${num(row.units / row.cost_rub, 2)} ед./₽` : '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
    </template>
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import UiSelect from '../../components/ui/UiSelect.vue'
import { adminApi } from '../../api/admin'
import { errorText } from '../../api/web'
import { pushToast } from '../../composables/useToasts'
import { formatDateTime, num, rubAi } from '../../utils/format'

const data = ref(null)
const busy = ref(false)

const feedbackTotal = computed(() => (data.value?.feedback || []).reduce((sum, m) => sum + m.total, 0))
const downShare = computed(() =>
  feedbackTotal.value ? data.value.feedback.reduce((sum, m) => sum + m.down, 0) / feedbackTotal.value : 0,
)
// Больше трети «вниз» — повод разобраться: жёлтым; больше половины — красным.
const downTone = computed(() => (!feedbackTotal.value ? '' : downShare.value > 0.5 ? 'tone-bad' : downShare.value > 0.33 ? 'tone-warn' : 'tone-ok'))
const pct = (share) => `${Math.round(share * 100)}%`

// Доля входа из кэша провайдера: « (кэш 82%)»; кэша не было — пусто.
function cacheShare(row) {
  if (!row.tokens_cached || !row.tokens_in) return ''
  return ` (кэш ${Math.round((row.tokens_cached * 100) / row.tokens_in)}%)`
}

function monthLabel(key) {
  const [y, m] = key.split('-').map(Number)
  const text = new Date(y, m - 1, 1).toLocaleDateString('ru-RU', { month: 'long', year: 'numeric' }).replace(/\s*г\.$/, '')
  return text.charAt(0).toUpperCase() + text.slice(1)
}

async function load(month = '') {
  busy.value = true
  try {
    data.value = await adminApi.usage(month)
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

onMounted(() => load())
</script>
