<template>
  <div class="board-regen" role="group" :aria-label="title">
    <form class="board-regen__ask" @submit.prevent="run">
      <input
        ref="field"
        v-model="comment"
        class="ai-input"
        maxlength="300"
        placeholder="Что не так — необязательно"
        aria-label="Что не так в текущих вариантах"
        @keydown.esc.prevent="emit('close')"
      />
      <button class="btn btn--xs" type="submit" :disabled="loading || board.disabled.value">
        {{ loading ? 'Думаю…' : variants ? 'Ещё варианты' : 'Предложить' }}
      </button>
      <button class="ai-check__link" type="button" @click="emit('close')">Закрыть</button>
    </form>
    <p class="hint board-regen__note">Один короткий запрос к ИИ — спишется из лимита, как обычный.</p>

    <template v-if="variants">
      <p v-if="!variants.length" class="hint">ИИ не предложил подходящего — уточните, что не так, и попробуйте ещё.</p>
      <div v-else class="board-regen__cols">
        <div v-if="olds.length" class="board-regen__col">
          <span class="board-regen__head">{{ line ? 'Сейчас' : 'Оставить' }}</span>
          <ul>
            <li v-for="item in olds" :key="item.text">
              <UiCheckbox v-if="!line" :checked="keep.has(item.text)" :aria-label="item.text" @change="flip(keep, item.text)" />
              <span>{{ item.text }}</span>
            </li>
          </ul>
        </div>
        <div class="board-regen__col board-regen__col--new">
          <span class="board-regen__head">Новые варианты</span>
          <ul>
            <li v-for="text in variants" :key="text">
              <label v-if="line" class="board-regen__radio">
                <input v-model="chosen" type="radio" :value="text" :name="`regen-${uid}`" />
                <span>{{ text }}</span>
              </label>
              <template v-else>
                <UiCheckbox :checked="picked.has(text)" :aria-label="text" @change="flip(picked, text)" />
                <span>{{ text }}</span>
              </template>
              <small v-if="limit" class="board-lines__len">{{ text.length }}/{{ limit }}</small>
            </li>
          </ul>
        </div>
      </div>
      <div v-if="variants.length" class="board-regen__foot">
        <small v-if="max && !line" :class="{ 'tone-bad': total > max }">будет {{ total }} из {{ max }}</small>
        <button class="btn btn--accent btn--xs" type="button" :disabled="!canApply" @click="apply">
          {{ line ? 'Заменить' : 'Поставить выбранное' }}
        </button>
        <small v-if="queued" class="hint">в записанном шаге правка встанет в «Применить в Директ»</small>
      </div>
    </template>
  </div>
</template>

<script setup>
/**
 * ↻ на доске: новые варианты рядом со старыми. Список (заголовки, тексты, фразы, минус-фразы, уточнения) —
 * галочками: старые снять, новые отметить; одна строка — выбрать замену. Варианты даёт один короткий вызов
 * модели (board.regenerate), на доску они ложатся обычной правкой (board.replace).
 */
import { computed, inject, nextTick, onMounted, reactive, ref } from 'vue'
import UiCheckbox from '../../components/ui/UiCheckbox.vue'

const props = defineProps({
  kind: { type: String, required: true },
  group: { type: String, default: '' },
  // Строки того же вида: старые варианты (у фраз и минус-фраз не показываем — их много, новые добавляются).
  items: { type: Array, default: () => [] },
  // Заменить одну строку (элемент доски) — иначе дополнить список.
  line: { type: Object, default: null },
  // Предел строк в списке (заголовки 7, тексты 3); null — без счётчика.
  max: { type: Number, default: null },
})
const emit = defineEmits(['close'])
const board = inject('board')

const LIMITS = { title: 56, text: 81, sitelink: 30, callout: 25 }
const SHOW_OLD = new Set(['title', 'text', 'callout'])
const TITLES = {
  title: 'Новые заголовки',
  text: 'Новые тексты',
  keyword: 'Новые фразы',
  negative: 'Новые минус-фразы',
  callout: 'Новые уточнения',
  sitelink: 'Новая быстрая ссылка',
}

const uid = Math.random().toString(36).slice(2, 8)
const field = ref(null)
const comment = ref('')
const loading = ref(false)
const variants = ref(null)
const chosen = ref('')
const picked = reactive(new Set())
const keep = reactive(new Set())

// Строки уже в черновике Директа — правка встанет в очередь «Применить в Директ».
const queued = computed(() => (props.line ? [props.line] : props.items).some((item) => item.in_direct === true))
const limit = computed(() => LIMITS[props.kind] || 0)
const title = computed(() => (props.line ? `Замена: ${props.line.text}` : TITLES[props.kind]))
const olds = computed(() => {
  if (props.line) return [props.line]
  return SHOW_OLD.has(props.kind) ? props.items.filter(board.isOn) : []
})
const total = computed(() => props.items.filter(board.isOn).length - (olds.value.length - keep.size) + picked.size)
const canApply = computed(() => {
  if (board.disabled.value || loading.value) return false
  if (props.line) return !!chosen.value
  if (!picked.size) return false
  return !props.max || total.value <= props.max
})

onMounted(async () => {
  for (const item of olds.value) keep.add(item.text)
  await nextTick()
  field.value?.focus()
})

function flip(set, text) {
  if (set.has(text)) set.delete(text)
  else set.add(text)
}

async function run() {
  if (loading.value) return
  loading.value = true
  try {
    const res = await board.regenerate({
      kind: props.kind,
      group: props.group,
      text: props.line?.text || '',
      comment: comment.value.trim(),
    })
    if (res === null) return
    variants.value = res
    picked.clear()
    chosen.value = ''
  } finally {
    loading.value = false
  }
}

async function apply() {
  if (!canApply.value) return
  const ok = props.line
    ? await board.replace(props.kind, props.group, [], [chosen.value], props.line)
    : await board.replace(
        props.kind,
        props.group,
        olds.value.filter((item) => !keep.has(item.text)),
        variants.value.filter((text) => picked.has(text)),
      )
  if (ok) emit('close')
}
</script>
