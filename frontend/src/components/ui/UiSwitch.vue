<template>
  <label class="ui-switch" :class="[rootAttrs.class, { 'is-disabled': disabled }]" :style="rootAttrs.style">
    <input v-bind="inputAttrs" class="ui-switch__input" type="checkbox" role="switch" :disabled="disabled" @change="onChange" />
    <span v-if="$slots.default" class="ui-switch__text"><slot /></span>
  </label>
</template>

<script setup>
import { useCheckModel } from './checkModel'

/**
 * Переключатель «включено/выключено»: показы кампании, правило, фильтр «Без конверсий», экспертный режим.
 * Подпись — слотом; без подписи нужен aria-label. Подключается как UiCheckbox: v-model или :checked + @change.
 */
defineOptions({ inheritAttrs: false })

const props = defineProps({
  modelValue: { type: Boolean, default: undefined },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue'])

const { rootAttrs, inputAttrs, onChange } = useCheckModel(props, emit)
</script>
