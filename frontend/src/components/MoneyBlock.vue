<template>
  <template v-if="known.length">
    <h2 class="section-h money-h">
      <span class="hico" aria-hidden="true"><Wallet /></span>Деньги <span class="cgroup__n">{{ known.length }}</span>
      <small class="section-h__note">{{ syncedNote }}</small>
      <button class="btn btn--ghost btn--sm money-h__act" type="button" :disabled="refreshing" @click="$emit('refresh')">
        <RefreshCw aria-hidden="true" />{{ refreshing ? 'Обновляем…' : 'Обновить баланс' }}
      </button>
    </h2>
    <ul class="money">
      <li v-for="b in known" :key="b.account_id" class="money__row">
        <div class="money__who">
          <b v-tip.truncate="b.name || b.login">{{ b.name || b.login }}</b>
          <small v-tip.truncate="whereOf(b)">{{ whereOf(b) }}</small>
        </div>
        <div class="money__sum">
          <b :class="toneOf(b)">{{ rub(b.balance_kopecks) }}</b>
          <small :class="toneOf(b)">{{ runwayText(b) }}</small>
          <small v-if="b.balance_error" class="tone-warn">баланс не обновился — показан прошлый</small>
        </div>
        <div class="money__acts">
          <a class="btn btn--ghost btn--sm" :href="b.direct_url" target="_blank" rel="noopener">
            Пополнить<ExternalLink aria-hidden="true" />
          </a>
          <button class="btn btn--ghost btn--sm" type="button" v-tip="'Скопировать вежливое напоминание для клиента'" @click="remind(b)">
            <Copy aria-hidden="true" />Напомнить клиенту
          </button>
        </div>
      </li>
    </ul>
    <p v-if="unknownNote" class="money__rest">{{ unknownNote }}</p>
  </template>
</template>

<script setup>
import { computed } from 'vue'
import { Copy, ExternalLink, RefreshCw, Wallet } from '@lucide/vue'
import { pushToast } from '../composables/useToasts'
import { asOfNote, balanceTone, daysLong, daysShort, isoDay, niceQuotes, rub } from '../utils/format'

/**
 * Блок «Деньги» главной: счёт каждого логина Директа — сколько осталось, на сколько дней хватит при расходе
 * последней недели и до какого числа. Первым — у кого деньги кончатся раньше (порядок приходит с сервера).
 * Пополняют в самом Директе: «Пополнить» открывает кабинет логина в новой вкладке.
 */
const props = defineProps({
  /** overview.balances: по логину — баланс, дни, дата, адрес кабинета. */
  balances: { type: Array, default: () => [] },
  refreshing: Boolean,
})
defineEmits(['refresh'])

const known = computed(() => props.balances.filter((b) => b.balance_kopecks != null))
const unknown = computed(() => props.balances.filter((b) => b.balance_kopecks == null))

// Баланс снимают раз в час вместе с расходом за сегодня; время — по самому старому логину.
const syncedNote = computed(() => {
  const times = known.value.map((b) => b.balance_synced_at).filter(Boolean).sort()
  return times.length ? `по данным Директа ${asOfNote(times[0])}` : ''
})

const unknownNote = computed(() => {
  if (!unknown.value.length) return ''
  const names = unknown.value.map((b) => b.name || b.login).join(', ')
  const loading = unknown.value.some((b) => !b.balance_synced_at)
  // Баланс пустой при снятом балансе — у логина нет общего счёта, деньги лежат на кампаниях.
  return loading
    ? `Ещё не знаем баланс: ${names}. Он появится после ближайшего обновления.`
    : `Без общего счёта: ${names}. Деньги у них лежат на кампаниях — смотрите в Директе.`
})

function whereOf(b) {
  const parts = [b.workspace_name ? niceQuotes(b.workspace_name) : '', b.name && b.login !== b.name ? b.login : '']
  return parts.filter(Boolean).join(' · ') || 'Без проекта'
}

function toneOf(b) {
  if (b.balance_kopecks <= 0) return 'tone-bad'
  return balanceTone(b.days_on_balance)
}

function dayMonth(iso) {
  return new Date(`${iso}T00:00:00`).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' })
}

function runwayText(b) {
  if (b.balance_kopecks <= 0) return 'деньги закончились — реклама стоит'
  if (b.days_on_balance == null) return 'за неделю расхода не было'
  const until = b.runs_out_on === isoDay(new Date()) ? 'закончатся сегодня' : `до ${dayMonth(b.runs_out_on)}`
  return `хватит примерно на ${daysShort(b.days_on_balance)} · ${until}`
}

/** Текст для клиента: остаток, на сколько хватит и просьба пополнить — без терминов. */
function reminderText(b) {
  const where = `На рекламном счёте в Яндекс Директе (логин ${b.login})`
  if (b.balance_kopecks <= 0) {
    return (
      `Здравствуйте! ${where} закончились деньги, и реклама остановилась. ` +
      'Пополните, пожалуйста, счёт — показы возобновятся сразу после оплаты. Спасибо!'
    )
  }
  let runway = ''
  if (b.days_on_balance != null) {
    const days = b.days_on_balance < 1 ? 'меньше чем на день' : `примерно на ${daysLong(Math.floor(b.days_on_balance))}`
    const until = b.runs_out_on && b.runs_out_on !== isoDay(new Date()) ? `, до ${dayMonth(b.runs_out_on)}` : ''
    runway = ` При текущем расходе этого хватит ${days}${until}.`
  }
  return (
    `Здравствуйте! ${where} осталось ${rub(b.balance_kopecks)}.${runway} ` +
    'Чтобы реклама не остановилась, пополните, пожалуйста, счёт заранее. Спасибо!'
  )
}

async function remind(b) {
  try {
    await navigator.clipboard.writeText(reminderText(b))
    pushToast('Напоминание скопировано — вставьте его в письмо или мессенджер клиенту')
  } catch {
    pushToast('Не удалось скопировать — браузер не дал доступ к буферу обмена', 'error')
  }
}
</script>

<style scoped>
.money-h {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0 8px;
}
.section-h__note {
  color: var(--faint);
  font-size: 12px;
  font-weight: 600;
}
.money-h__act {
  margin-left: auto;
}
.money {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.money__row {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(0, 1.4fr) auto;
  align-items: center;
  gap: 8px 16px;
  padding: 12px 16px;
  border: 1px solid var(--line);
  border-radius: var(--r-card);
  background: var(--surface);
}
.money__who,
.money__sum {
  display: grid;
  min-width: 0;
  gap: 2px;
}
.money__who b,
.money__who small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.money__who small,
.money__sum small {
  color: var(--muted);
  font-size: 13px;
}
.money__sum b {
  font-size: 17px;
  font-weight: 800;
  font-variant-numeric: tabular-nums;
}
/* Серый подписи не должен перебивать тон: красный — деньги кончились или почти, жёлтый — меньше 5 дней. */
.money__sum small.tone-bad {
  color: var(--danger);
}
.money__sum small.tone-warn {
  color: var(--warn);
}
.money__acts {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}
.money__rest {
  margin: 8px 0 0;
  color: var(--faint);
  font-size: 13px;
}
@media (max-width: 720px) {
  .money__row {
    grid-template-columns: minmax(0, 1fr);
  }
  .money__acts {
    justify-content: flex-start;
  }
}
</style>
