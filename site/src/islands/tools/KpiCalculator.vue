<script setup>
// Калькулятор CPA, ДРР и ROMI. Пустые поля — показатель не считается, а не превращается в ноль.
import { computed, ref } from 'vue'

const spend = ref(60000)
const clicks = ref(2000)
const leads = ref(80)
const sales = ref(24)
const revenue = ref(360000)
const margin = ref(40)

const num = (v) => (v === '' || v === null || v === undefined ? null : Number(v))
const div = (a, b) => (a === null || b === null || !b ? null : a / b)
const rub = (v) => (v === null ? '—' : `${Math.round(v).toLocaleString('ru-RU')} ₽`)
const pct = (v) => (v === null ? '—' : `${(Math.round(v * 10) / 10).toLocaleString('ru-RU')} %`)

const r = computed(() => {
  const s = num(spend.value)
  const c = num(clicks.value)
  const l = num(leads.value)
  const o = num(sales.value)
  const rev = num(revenue.value)
  const m = num(margin.value)
  const profit = rev === null ? null : m === null ? rev : (rev * m) / 100
  return {
    cpc: div(s, c),
    cr: div(l, c) === null ? null : div(l, c) * 100,
    cpa: div(s, l),
    cpo: div(s, o),
    drr: div(s, rev) === null ? null : div(s, rev) * 100,
    romi: profit === null || !s ? null : ((profit - s) / s) * 100,
    byRevenue: m === null,
  }
})
</script>

<template>
  <div class="tool">
    <div class="tool__panel">
      <span class="tool__cap">Данные за период</span>
      <div class="tool__field">
        <label class="tool__label" for="kc-s">Расход на рекламу, ₽</label>
        <input id="kc-s" v-model="spend" type="number" min="0" inputmode="decimal" />
      </div>
      <div class="tool__field">
        <label class="tool__label" for="kc-c">Клики</label>
        <input id="kc-c" v-model="clicks" type="number" min="0" inputmode="numeric" />
      </div>
      <div class="tool__field">
        <label class="tool__label" for="kc-l">Заявки <span class="tool__hint">конверсии по цели</span></label>
        <input id="kc-l" v-model="leads" type="number" min="0" inputmode="numeric" />
      </div>
      <div class="tool__field">
        <label class="tool__label" for="kc-o">Продажи</label>
        <input id="kc-o" v-model="sales" type="number" min="0" inputmode="numeric" />
      </div>
      <div class="tool__field">
        <label class="tool__label" for="kc-r">Выручка с рекламы, ₽</label>
        <input id="kc-r" v-model="revenue" type="number" min="0" inputmode="decimal" />
      </div>
      <div class="tool__field">
        <label class="tool__label" for="kc-m">Маржинальность, % <span class="tool__hint">пусто — ROMI по выручке</span></label>
        <input id="kc-m" v-model="margin" type="number" min="0" max="100" inputmode="decimal" />
      </div>
    </div>

    <div class="tool__panel tool__panel--out dark" aria-live="polite">
      <span class="tool__cap">Показатели</span>
      <div class="tool__stat"><span>CPA — цена заявки</span><b>{{ rub(r.cpa) }}</b></div>
      <div class="tool__stat"><span>ДРР — доля расходов в выручке</span><b>{{ pct(r.drr) }}</b></div>
      <div class="tool__stat">
        <span>ROMI{{ r.byRevenue ? ' по выручке' : ' по прибыли' }}</span><b>{{ pct(r.romi) }}</b>
      </div>
      <div class="tool__stat"><span>Цена продажи</span><b>{{ rub(r.cpo) }}</b></div>
      <div class="tool__stat"><span>CPC — цена клика</span><b>{{ rub(r.cpc) }}</b></div>
      <div class="tool__stat"><span>Конверсия клика в заявку</span><b>{{ pct(r.cr) }}</b></div>
      <p class="tool__note">
        CPA = расход ÷ заявки. ДРР = расход ÷ выручка × 100. ROMI = (выручка × маржа − расход) ÷ расход × 100.
      </p>
    </div>
  </div>
</template>
