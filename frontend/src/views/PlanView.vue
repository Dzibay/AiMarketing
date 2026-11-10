<template>
  <AppShell>
    <div class="cabinet__head">
      <div>
        <h1>Тариф</h1>
        <p>Условия, расход лимита за период и переход на другой тариф.</p>
      </div>
    </div>

    <div v-if="loading" class="panel"><p class="empty">Загружаем…</p></div>

    <div v-else-if="loadError" class="panel">
      <p class="empty">{{ loadError }}</p>
      <button class="btn btn--ghost" type="button" @click="load">Повторить</button>
    </div>

    <template v-else-if="data">
      <div v-if="sub.state === 'expired'" class="notice notice--warn">
        <strong>Подписка закончилась {{ formatDay(sub.last_day) }}.</strong>
        Кабинет закрыт, но все данные сохранены. Загрузка из Яндекса и правила остановлены — после продления догрузим
        пропущенное за время перерыва.
      </div>

      <section class="panel">
        <div class="panel__head">
          <div>
            <h2>{{ sub.plan ? sub.plan.name : 'Без тарифа' }}</h2>
            <p class="hint">{{ termText }}</p>
          </div>
          <span class="chip" :class="stateChip.cls">{{ stateChip.text }}</span>
        </div>
        <ul class="plan-facts">
          <li>
            <span>Проекты</span>
            <strong>{{ sub.projects_limit === null ? 'без ограничений' : `до ${sub.projects_limit}` }}</strong>
          </li>
          <li>
            <span>ИИ-ассистент, Вордстат и картинки</span>
            <strong>{{ sub.units_limited ? 'лимит на период' : 'без лимита' }}</strong>
          </li>
          <li v-for="(label, key) in data.features" :key="key" :class="{ 'is-off': sub.features?.[key] === false }">
            <span>{{ label }}</span>
            <strong v-if="sub.features?.[key] === false"><Lock aria-hidden="true" /> на тарифе выше</strong>
            <strong v-else><Check aria-hidden="true" /> есть</strong>
          </li>
        </ul>
      </section>

      <UsagePanel v-if="data.usage" :usage="data.usage" />

      <section class="panel" id="offers">
        <div class="panel__head">
          <h2>{{ sub.state === 'expired' ? 'Продлить или сменить тариф' : 'Тарифы' }}</h2>
        </div>
        <p class="hint">Цена — за срок тарифа; скидка за переход с вашего тарифа уже учтена.</p>
        <div v-if="chosen" class="notice plan-chosen" role="status">
          <span>
            Онлайн-оплата скоро появится. Чтобы {{ chosen.current ? 'продлить' : 'перейти на' }} «{{ chosen.name }}»
            сейчас, напишите в поддержку — переключим тариф вручную.
          </span>
          <button class="icon-btn" type="button" aria-label="Скрыть" @click="chosen = null">
            <X aria-hidden="true" />
          </button>
        </div>
        <p v-if="!data.offers.length" class="empty">Тарифы ещё не настроены — напишите в поддержку, подберём условия.</p>
        <PlanOffers v-else :offers="data.offers" :features="data.features" @choose="chosen = $event" />
      </section>
    </template>
  </AppShell>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { Check, Lock, X } from '@lucide/vue'
import AppShell from '../components/AppShell.vue'
import PlanOffers from '../components/PlanOffers.vue'
import UsagePanel from '../components/UsagePanel.vue'
import { errorText } from '../api/web'
import { loadPlan, usePlan } from '../composables/usePlan'
import { loadWorkspaces } from '../composables/useWorkspaces'
import { useHead } from '../utils/useHead'
import { formatDay } from '../utils/format'

useHead('Тариф — AiMarketing', true)

const { plan: data } = usePlan()
const loading = ref(!data.value)
const loadError = ref('')
const chosen = ref(null)

const sub = computed(() => data.value?.subscription || {})

const termText = computed(() => {
  const s = sub.value
  if (s.state === 'expired') return `Закончилась ${formatDay(s.last_day)}`
  if (s.last_day) return `Действует по ${formatDay(s.last_day)} включительно`
  return 'Бессрочно'
})

const stateChip = computed(() => {
  const s = sub.value
  if (s.state === 'expired') return { text: 'закончилась', cls: 'chip--bad' }
  if (s.plan?.is_trial) return { text: 'пробный', cls: 'chip--info' }
  return { text: 'действует', cls: 'chip--ok' }
})

async function load() {
  loadError.value = ''
  try {
    const fresh = await loadPlan(true)
    // Продлили, пока была открыта эта страница, — вернём переключатель проектов в шапку.
    if (fresh.subscription?.state === 'ok') loadWorkspaces().catch(() => null)
  } catch (err) {
    loadError.value = errorText(err)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
