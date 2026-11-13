<template>
  <label class="ui-check" :class="[rootAttrs.class, { 'is-disabled': disabled }]" :style="rootAttrs.style">
    <input
      v-bind="inputAttrs"
      class="ui-check__box"
      type="checkbox"
      :disabled="disabled"
      :indeterminate="indeterminate"
      @change="onChange"
    />
    <span v-if="$slots.default" class="ui-check__text"><slot /></span>
  </label>
</template>

<script setup>
import { useCheckModel } from './checkModel'

/**
 * Флажок: выбрать строку, отметить пункт, согласиться. Для «включено/выключено» — UiSwitch.
 * Подпись — слотом (<small> внутри — пояснение второй строкой); без подписи нужен aria-label.
 * indeterminate — «выбраны не все» у флажка «Выбрать все».
 */
defineOptions({ inheritAttrs: false })

const props = defineProps({
  modelValue: { type: [Boolean, Array], default: undefined },
  value: { type: [String, Number, Boolean, Object], default: undefined },
  indeterminate: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue'])

const { rootAttrs, inputAttrs, onChange } = useCheckModel(props, emit)
</script>
