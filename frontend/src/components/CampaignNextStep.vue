<template>
  <a
    v-if="next.kind === 'topup'"
    class="btn"
    :class="classes"
    :href="campaign.money_url || DIRECT_URL"
    target="_blank"
    rel="noopener"
  >
    Пополнить в Директе
  </a>
  <RouterLink
    v-else-if="next.kind === 'fix'"
    class="btn"
    :class="classes"
    :to="moderationFixLink(wsId, cid, campaign.name)"
  >
    <Sparkles aria-hidden="true" />{{ next.label }}
  </RouterLink>
  <button
    v-else-if="next.kind === 'moderate' || next.kind === 'resume'"
    class="btn"
    :class="classes"
    type="button"
    :disabled="busy"
    @click="next.kind === 'moderate' ? moderate() : resume()"
  >
    <component :is="next.kind === 'moderate' ? Send : Play" aria-hidden="true" />{{ next.label }}
  </button>
</template>

<script setup>
import { computed, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { Play, Send, Sparkles } from '@lucide/vue'
import { askConfirm } from '../composables/useConfirm'
import { pushToast } from '../composables/useToasts'
import { undoAction } from '../composables/useUndo'
import { errorText, wsApi } from '../api/web'
import { confirmCampaignToggle } from '../utils/campaignConfirm'
import { DIRECT_URL, moderationFixLink } from '../utils/format'

/**
 * Одна кнопка следующего шага для кампании, которая не показывается (campaignReason → next): отправить черновик
 * на проверку, исправить отказ модерации с ИИ, пополнить счёт или включить. После действия — done: страница
 * перечитывает кампанию.
 */
const props = defineProps({
  wsId: { type: Number, required: true },
  // Строка кампании: campaign_row_id (или id), name, state, money_url.
  campaign: { type: Object, required: true },
  next: { type: Object, required: true },
  small: Boolean,
  // Вторая кнопка рядом с главной — без акцента.
  quiet: Boolean,
})
const emit = defineEmits(['done'])

const busy = ref(false)
const classes = computed(() => ({ 'btn--accent': !props.quiet, 'btn--sm': props.small }))
const cid = computed(() => Number(props.campaign.campaign_row_id ?? props.campaign.id))

async function moderate() {
  const name = props.campaign.name || 'кампанию'
  const ok = await askConfirm(
    `Отправить «${name}» на проверку в Директ? Когда модерация её примет, начнутся показы и будет тратиться бюджет.`,
    { title: 'Отправить на проверку', confirmText: 'Отправить' },
  )
  if (!ok) return
  busy.value = true
  try {
    const res = await wsApi.moderate(props.wsId, cid.value)
    const errors = res.errors || []
    if (!res.drafts) pushToast('Отправлять нечего — всё уже на проверке или принято')
    else if (errors.length) pushToast(`Отправлено ${res.sent} из ${res.drafts}. Не ушло: ${errors.slice(0, 2).join('; ')}`, 'error')
    else pushToast('Отправлено на проверку — Директ обычно проверяет в течение дня', 'ok')
    emit('done')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

async function resume() {
  if (!(await confirmCampaignToggle({ name: props.campaign.name, state: props.campaign.state }))) return
  busy.value = true
  try {
    const res = await wsApi.bulk(props.wsId, { campaign_ids: [cid.value], action: 'resume' })
    const error = (res.errors || [])[0]
    if (error) pushToast(error.message || errorText({ code: error.error }), 'error')
    else pushToast('Кампания включена', 'ok', undoAction(res, () => emit('done')))
    emit('done')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}
</script>
