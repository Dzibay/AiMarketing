import { computed, getCurrentInstance, nextTick, useAttrs } from 'vue'

/**
 * Общая логика UiCheckbox и UiSwitch. Внутри — настоящий input type=checkbox: клавиатура, формы и экранный диктор
 * работают как у системного флажка, меняется только вид.
 *
 * Два способа подключения:
 * - v-model: boolean или массив (тогда value — значение этого флажка в массиве);
 * - как у input: :checked и @change="… $event.target.checked" — все атрибуты и обработчики, кроме class и style,
 *   уходят на сам input, поэтому старые обработчики работают без изменений.
 */
export function useCheckModel(props, emit) {
  const attrs = useAttrs()
  // v-model узнаём по слушателю, а не по значению: у нового пункта значение ещё undefined.
  const bound = 'onUpdate:modelValue' in (getCurrentInstance().vnode.props || {})
  const checked = computed(() =>
    Array.isArray(props.modelValue) ? props.modelValue.includes(props.value) : !!props.modelValue,
  )

  const rootAttrs = computed(() => ({ class: attrs.class, style: attrs.style }))
  const inputAttrs = computed(() => {
    const { class: _class, style: _style, ...rest } = attrs
    return bound ? { ...rest, checked: checked.value } : rest
  })

  function onChange(event) {
    if (!bound) return
    const on = event.target.checked
    if (Array.isArray(props.modelValue)) {
      const rest = props.modelValue.filter((v) => v !== props.value)
      emit('update:modelValue', on ? [...rest, props.value] : rest)
    } else {
      emit('update:modelValue', on)
    }
    // Если родитель значение не принял, флажок возвращается к тому, что в модели.
    nextTick(() => {
      if (event.target.checked !== checked.value) event.target.checked = checked.value
    })
  }

  return { rootAttrs, inputAttrs, onChange }
}
