export async function request(path, options = {}) {
  const { body, ...init } = options
  const response = await fetch('/api' + path, {
    ...init,
    ...(body === undefined ? {} : {
      method: init.method || 'POST',
      headers: { 'Content-Type': 'application/json', ...init.headers },
      body: JSON.stringify(body),
    }),
  })
  if (!response.ok) {
    const error = await response.json().catch(() => ({}))
    const detail = Array.isArray(error.detail)
      ? error.detail.map(item => item.msg).join('. ')
      : error.detail
    throw new Error(typeof detail === 'string' ? detail : `Request failed (${response.status}).`)
  }
  return response
}

function inquirePath(matterId) {
  return `/matters/${encodeURIComponent(matterId)}/inquire`
}

export async function askMatter(matterId, body, signal) {
  return (await request(inquirePath(matterId), { body, signal })).json()
}

export async function exportAnswer(matterId, answer, format) {
  const response = await request(inquirePath(matterId) + '/export', {
    body: { question: answer.question, rows: answer.rows, format },
  })
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = url
  link.download = `matter-answers.${format}`
  document.body.append(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
