/**
 * Картинка из файла, уменьшенная в браузере: длинная сторона до maxSide, JPEG на белом фоне;
 * не влезла в maxBytes — качество ниже. → { dataUrl, width, height }.
 * Ошибки: Error('image') — не картинка или не читается, Error('too-big') — не ужалась.
 */
export async function shrinkImage(file, { maxSide = 2000, maxBytes = 3_800_000 } = {}) {
  const url = URL.createObjectURL(file)
  try {
    const img = await new Promise((resolve, reject) => {
      const el = new Image()
      el.onload = () => resolve(el)
      el.onerror = () => reject(new Error('image'))
      el.src = url
    })
    const scale = Math.min(1, maxSide / Math.max(img.naturalWidth, img.naturalHeight))
    const canvas = document.createElement('canvas')
    canvas.width = Math.max(1, Math.round(img.naturalWidth * scale))
    canvas.height = Math.max(1, Math.round(img.naturalHeight * scale))
    const ctx = canvas.getContext('2d')
    ctx.fillStyle = '#fff'
    ctx.fillRect(0, 0, canvas.width, canvas.height)
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
    for (const quality of [0.88, 0.75, 0.6, 0.45]) {
      const dataUrl = canvas.toDataURL('image/jpeg', quality)
      if (dataUrl.length * 0.75 <= maxBytes) return { dataUrl, width: canvas.width, height: canvas.height }
    }
    throw new Error('too-big')
  } finally {
    URL.revokeObjectURL(url)
  }
}
