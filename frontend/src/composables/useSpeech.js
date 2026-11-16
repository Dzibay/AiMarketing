import { onBeforeUnmount, ref } from 'vue'

/**
 * Голосовой ввод через SpeechRecognition браузера (ru-RU). Текст появляется по ходу речи: onText получает
 * всё сказанное с начала записи — окончательные куски и текущий черновой. Отправляет человек сам.
 * В Chrome распознавание идёт на серверах Google — об этом подсказка у кнопки и статья в справке.
 * supported = false — браузер не умеет, кнопку не показываем.
 */
export function useSpeech(onText) {
  const Recognition = typeof window !== 'undefined' ? window.SpeechRecognition || window.webkitSpeechRecognition : null
  const supported = !!Recognition
  const listening = ref(false)
  const error = ref('')
  let rec = null

  function start() {
    if (!supported || listening.value) return
    error.value = ''
    rec = new Recognition()
    rec.lang = 'ru-RU'
    rec.continuous = true
    rec.interimResults = true
    rec.onresult = (event) => {
      let said = ''
      for (let i = 0; i < event.results.length; i += 1) said += event.results[i][0].transcript
      onText(said.replace(/\s+/g, ' ').trim())
    }
    rec.onerror = (event) => {
      const messages = {
        'not-allowed': 'Нет доступа к микрофону — разрешите его в настройках браузера',
        'service-not-allowed': 'Браузер не разрешает распознавание речи',
        'audio-capture': 'Микрофон не найден',
        network: 'Нет связи с сервисом распознавания',
      }
      // no-speech и aborted — не ошибки: человек промолчал или сам остановил запись.
      if (messages[event.error]) error.value = messages[event.error]
    }
    rec.onend = () => {
      listening.value = false
      rec = null
    }
    try {
      rec.start()
      listening.value = true
    } catch {
      rec = null
    }
  }

  function stop() {
    rec?.stop()
  }

  function toggle() {
    if (listening.value) stop()
    else start()
  }

  onBeforeUnmount(() => rec?.abort())

  return { supported, listening, error, start, stop, toggle }
}
