import { useEffect, useMemo, useRef, useState } from 'react'
import { askMatter, exportAnswer } from './api'

const templates = [
  ['timeline', 'Communication Timeline', 'What communications establish the sequence of events? Include dates, participants, and what each communication shows.'],
  ['people', 'Key People', 'Who are the key people mentioned in the evidence, and what roles or actions do the documents attribute to them?'],
  ['conflicts', 'Conflicting Accounts', 'Which documents give differing accounts of the same event? Explain the difference and cite each account separately.'],
  ['decisions', 'Actions And Decisions', 'What decisions, commitments, or action items are documented? Identify owners and timing where the evidence supports them.'],
]
const pageSize = 8

function Scope({ documents, selected, setSelected, limited, setLimited, disabled }) {
  const [filter, setFilter] = useState('')
  const [page, setPage] = useState(0)
  const matching = useMemo(() => documents.filter(doc => doc.name.toLowerCase().includes(filter.trim().toLowerCase())), [documents, filter])
  const lastPage = Math.max(0, Math.ceil(matching.length / 20) - 1)
  const currentPage = Math.min(page, lastPage)
  function toggle(id) {
    setSelected(previous => previous.includes(id) ? previous.filter(value => value !== id) : [...previous, id])
  }
  return <details className="wi-scope">
    <summary>Document Scope <span>{limited ? `${selected.length} selected` : `All ${documents.length.toLocaleString()} eligible documents`}</span></summary>
    <fieldset disabled={disabled}>
      <label className="wi-check"><input type="checkbox" checked={limited} onChange={event => setLimited(event.target.checked)} /> Limit To Selected Documents</label>
      {limited && <>
        <label>Find Documents<input type="search" value={filter} onChange={event => { setFilter(event.target.value); setPage(0) }} /></label>
        <div className="wi-doc-list">
          {matching.slice(currentPage * 20, (currentPage + 1) * 20).map(doc =>
            <label key={doc.id} className="wi-check"><input type="checkbox" checked={selected.includes(doc.id)}
              disabled={selected.length >= 500 && !selected.includes(doc.id)} onChange={() => toggle(doc.id)} />{doc.name}</label>)}
          {!matching.length && <p>No documents match this filter.</p>}
        </div>
        {matching.length > 20 && <div className="wi-pager">
          <button type="button" className="secondary" disabled={!currentPage} onClick={() => setPage(currentPage - 1)}>Previous</button>
          <span>Page {currentPage + 1} of {lastPage + 1}</span>
          <button type="button" className="secondary" disabled={currentPage === lastPage} onClick={() => setPage(currentPage + 1)}>Next</button>
        </div>}
        {selected.length > 0 && <button type="button" className="secondary" onClick={() => setSelected([])}>Clear Selection</button>}
      </>}
    </fieldset>
  </details>
}

function Answer({ answer, layout, onReset, onExport, exporting }) {
  const [page, setPage] = useState(0)
  const label = { timeline: 'Date', people: 'Person', conflicts: 'Issue', decisions: 'Action Or Decision' }[layout] || 'Topic'
  const rows = answer.rows.slice(page * pageSize, (page + 1) * pageSize)
  return <div className="wi-answer">
    <div className="wi-answer-heading">
      <div><span className="wi-eyebrow">AI Draft · Review Sources</span><h3>Results</h3></div>
      <div className="wi-actions">
        <button type="button" className="secondary" onClick={onReset}>New Question</button>
        {answer.rows.length > 0 && ['csv', 'xlsx'].map(format =>
          <button key={format} type="button" className="secondary" disabled={exporting}
            onClick={() => onExport(format)}>{format === 'csv' ? 'Export CSV' : 'Export Excel'}</button>)}
      </div>
    </div>
    <p className="wi-question-recap">{answer.question}</p>
    <p className="hint">
      Used {answer.passages_used.toLocaleString()} of {answer.passages_in_scope.toLocaleString()} eligible {answer.passages_in_scope === 1 ? 'passage' : 'passages'} across {answer.documents_in_scope.toLocaleString()} {answer.documents_in_scope === 1 ? 'document' : 'documents'}.
      Results reflect the passages retrieved for this question.
    </p>
    {answer.warning && <p className="warning">{answer.warning}</p>}
    {answer.discarded_rows > 0 && <p className="warning">{answer.discarded_rows} model result(s) had unverifiable quotes and were omitted.</p>}
    {!rows.length ? <p className="wi-empty">No supported answer was found. Try a more specific question or adjust the document scope.</p> :
      <div className="table-wrap"><table>
        <thead><tr><th scope="col">{label}</th><th scope="col">Finding</th><th scope="col">Source</th></tr></thead>
        <tbody>{rows.map((row, index) => <tr key={`${row.chunk_id}-${index}`}>
          <td>{row.label}</td><td>{row.text}</td>
          <td><a href={row.citation} target="_blank" rel="noreferrer">{row.document_name} · {row.locator}</a>
            <details><summary>Supporting Quote</summary><blockquote>{row.quote}</blockquote></details>
          </td>
        </tr>)}</tbody>
      </table></div>}
    {answer.rows.length > pageSize && <div className="wi-pager">
      <button type="button" className="secondary" disabled={!page} onClick={() => setPage(page - 1)}>Previous</button>
      <span>{page * pageSize + 1}–{Math.min((page + 1) * pageSize, answer.rows.length)} of {answer.rows.length}</span>
      <button type="button" className="secondary" disabled={(page + 1) * pageSize >= answer.rows.length} onClick={() => setPage(page + 1)}>Next</button>
    </div>}
  </div>
}

export function Inquire({ matterId, matterName, documents, loading, visible }) {
  const [question, setQuestion] = useState('')
  const [layout, setLayout] = useState('answer')
  const [limited, setLimited] = useState(false)
  const [selected, setSelected] = useState([])
  const [answer, setAnswer] = useState(null)
  const [busy, setBusy] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [exporting, setExporting] = useState(false)
  const [error, setError] = useState('')
  const pending = useRef(null)
  const heading = useRef(null)
  const input = useRef(null)
  const effective = useMemo(() => {
    const eligible = new Set(documents.map(doc => doc.id))
    return selected.filter(id => eligible.has(id))
  }, [documents, selected])
  useEffect(() => () => pending.current?.abort(), [])
  useEffect(() => {
    if (!busy) return
    const start = Date.now()
    const timer = setInterval(() => setElapsed(Math.floor((Date.now() - start) / 1000)), 1000)
    return () => clearInterval(timer)
  }, [busy])
  useEffect(() => {
    if (visible && answer) heading.current?.focus()
  }, [answer, visible])

  async function ask(event) {
    event.preventDefault()
    if (pending.current || !question.trim() || !matterId || loading || !documents.length || (limited && !effective.length)) return
    const controller = new AbortController()
    pending.current = controller
    setError('')
    setElapsed(0)
    setBusy(true)
    try {
      const result = await askMatter(matterId, { question, layout, document_ids: limited ? effective : null }, controller.signal)
      if (!controller.signal.aborted) setAnswer(result)
    } catch (err) {
      if (!controller.signal.aborted) setError(err.message)
    } finally {
      if (pending.current === controller) {
        pending.current = null
        setBusy(false)
      }
    }
  }

  async function download(format) {
    setError('')
    setExporting(true)
    try { await exportAnswer(matterId, answer, format) }
    catch (err) { setError(err.message) }
    finally { setExporting(false) }
  }

  return <div className="wi-inquire" data-preserve-case>
    <div className="wi-inquire-intro" ref={heading} tabIndex={-1}>
      <div><span className="wi-eyebrow">Evidence Research</span><h2>Ask This Matter</h2>
        <p>Ask a question about {matterName || 'the selected matter'} and open the passages behind each result.</p></div>
      <span className="wi-local-badge">Ollama · Local</span>
    </div>
    {error && <p className="warning" role="alert">{error}</p>}
    {answer ? <Answer answer={answer} layout={layout} exporting={exporting} onExport={download}
      onReset={() => {
        setAnswer(null)
        setQuestion('')
        setLayout('answer')
        setError('')
        requestAnimationFrame(() => input.current?.focus())
      }} /> :
      <form onSubmit={ask}>
        <label>Prompt Template<select value={layout} disabled={busy} onChange={event => {
          const next = event.target.value
          setLayout(next)
          const template = templates.find(([id]) => id === next)
          if (template) setQuestion(template[2])
        }}>
          <option value="answer">Custom Question</option>
          {templates.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
        </select></label>
        <label>Your Question<textarea ref={input} rows={4} required maxLength={2000} disabled={busy}
          value={question} onChange={event => setQuestion(event.target.value)}
          placeholder="For example, what evidence shows who approved the change?" /></label>
        <Scope documents={documents} selected={effective} setSelected={setSelected} limited={limited}
          setLimited={setLimited} disabled={busy || loading} />
        <div className="wi-actions">
          <button type="submit" disabled={busy || loading || !matterId || !documents.length || !question.trim() || (limited && !effective.length)}>Ask Question</button>
          {busy && <span role="status">Searching and drafting locally · {elapsed}s elapsed</span>}
          {!loading && !documents.length && <span className="hint">Import evidence to ask a question.</span>}
        </div>
      </form>}
  </div>
}
