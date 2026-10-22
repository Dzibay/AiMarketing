export async function fetchSiteConfig() {
  const response = await fetch('/api/site/config')
  if (!response.ok) throw new Error('config')
  return response.json()
}

export async function fetchLegalPage(page) {
  const response = await fetch(`/api/site/legal/${page}`)
  if (!response.ok) throw new Error('legal')
  return response.json()
}
