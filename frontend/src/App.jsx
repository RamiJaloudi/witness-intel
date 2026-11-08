import { useLayoutEffect, useState, useSyncExternalStore } from 'react'
import { createPortal } from 'react-dom'
import { Inquire } from './Inquire'

const groups = [
  ['Evidence', [['Import', 'Evidence Library'], ['Ask This Matter', 'Ask This Matter'], ['Privacy', 'Privacy Review']]],
  ['Work Product', [['Witnesses', 'Witnesses'], ['Review', 'Review Queue'], ['Case', 'Issues And Timeline']]],
  ['Administration', [['Performance', 'Performance'], ['Quality', 'Quality'], ['Backups', 'Backups'], ['Audit', 'Audit'], ['Settings', 'Settings']]],
]

const tools = [
  ['Import', 'Evidence Library', 'Import documents and open the original sources.'],
  ['Ask This Matter', 'Ask This Matter', 'Ask a question and read the supporting passages.'],
  ['Witnesses', 'Witnesses', 'Prepare witness profiles and examination questions.'],
  ['Review', 'Review Queue', 'Inspect citations and review saved work product.'],
]

function WorkProductHeader({ label, title, description, state }) {
  return <div className="wi-inquire-intro wi-work-product-header">
    <div><span className="wi-eyebrow">{label}</span><h2>{title}</h2><p>{description}</p></div>
    {state.matterName && <span className="wi-local-badge">{state.matterName}</span>}
  </div>
}

export function App({ bridge }) {
  const state = useSyncExternalStore(bridge.subscribe, bridge.snapshot)
  const [menuOpen, setMenuOpen] = useState(false)
  const [scopeOpen, setScopeOpen] = useState(false)
  useLayoutEffect(() => {
    document.body.classList.add('react-workbench-ready')
    return () => document.body.classList.remove('react-workbench-ready')
  }, [])

  function navigate(page) {
    bridge.navigate(page)
    setMenuOpen(false)
  }

  return <>
    <button className="secondary wi-menu-toggle" type="button" aria-expanded={menuOpen}
      onClick={() => setMenuOpen(!menuOpen)}>Workbench Menu</button>
    <aside className={`wi-sidebar${menuOpen ? ' wi-sidebar-open' : ''}`} aria-label="Workbench">
      <button className="wi-brand" type="button" onClick={() => navigate('Overview')}>
        <span className="wi-brand-mark" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
            <path d="m12 2 9 5v6c0 5-9 9-9 9S3 18 3 13V7z"/><path d="m7 12 3 3 7-7"/>
          </svg>
        </span>
        <span>Witness Intel<small>Evidence Workbench</small></span>
      </button>
      <div className="wi-matter-card">
        <span className="wi-eyebrow">Current Matter</span>
        <strong title={state.matterName}>{state.matterName || 'Choose a Matter'}</strong>
        <span>{state.loading ? 'Loading document scope…' : `${state.documentCount.toLocaleString()} eligible documents`}</span>
        <div className="wi-matter-actions">
          <button type="button" className="secondary" aria-expanded={scopeOpen}
            onClick={() => setScopeOpen(!scopeOpen)}>Change Matter</button>
          <button type="button" className="secondary" disabled={state.workspaceToolsDisabled} onClick={() => {
            bridge.workspaceTool(0)
            setMenuOpen(false)
          }}>New Workspace</button>
        </div>
        {scopeOpen && <label>Matter
          <select value={state.matterId} onChange={event => {
            bridge.selectMatter(event.target.value)
            setScopeOpen(false)
          }}>
            {!state.matters.length && <option value="">No matters available</option>}
            {state.matters.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
        </label>}
        <div className="wi-matter-actions">
          <button type="button" className="secondary" disabled={state.workspaceToolsDisabled}
            onClick={() => bridge.workspaceTool(1)}>Manage Workspaces</button>
          <button type="button" className="secondary" disabled={state.workspaceToolsDisabled}
            onClick={() => bridge.workspaceTool(2)}>Workspace Trash</button>
        </div>
      </div>
      <nav className="wi-navigation" aria-label="Main Navigation">
        <button type="button" className="secondary" aria-current={state.page === 'Overview' ? 'page' : undefined}
          onClick={() => navigate('Overview')}>Overview</button>
        {groups.map(([heading, links]) => <div key={heading} className="wi-nav-group">
          <span className="wi-eyebrow">{heading}</span>
          {links.map(([page, label]) => <button key={page} type="button" className="secondary"
            aria-current={state.page === page ? 'page' : undefined} onClick={() => navigate(page)}>{label}</button>)}
        </div>)}
      </nav>
    </aside>
    {createPortal(<div className="wi-tool-grid">
      {tools.map(([page, title, text]) => <button key={page} type="button" className="secondary wi-tool-card"
        onClick={() => navigate(page)}><strong>{title}</strong><span>{text}</span><span aria-hidden="true">→</span></button>)}
    </div>, document.getElementById('reactOverview'))}
    {createPortal(<Inquire key={state.matterId} matterId={state.matterId} matterName={state.matterName}
      documents={state.documents} loading={state.loading} visible={state.page === 'Ask This Matter'} />,
    document.getElementById('reactInquire'))}
    {createPortal(<WorkProductHeader label="Witness Intelligence" title="Witness Work Product"
      description="Open a witness to review findings, draft examination questions, or export saved work. Review identities and citations before relying on them."
      state={state} />, document.getElementById('reactWitnessHeader'))}
    {createPortal(<WorkProductHeader label="Attorney Workbench" title="Review Queue"
      description="Inspect sources before approving findings, work products, or questions. Draft batches from interrupted jobs are marked incomplete."
      state={state} />, document.getElementById('reactReviewHeader'))}
  </>
}
