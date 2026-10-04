import { useEffect, useRef, useState } from 'react'
import type { ChangeEvent, DragEvent, ReactNode } from 'react'
import { caseApi, uploadCase, validatePdf } from './api/client'
import { useCaseEvents } from './api/hooks'
import { useCaseEvents as useLiveCaseEvents } from './api/useCaseEvents'
import type { CaseFixture, CaseStage, Finding, PolicyFacts, ReviewAction, ReviewLogEntry } from './api/types'
import { demoCase } from './mocks/fixtures'
import './App.css'

type Screen = 'upload' | 'facts' | 'case' | 'eval'
type DocumentKind = 'policy' | 'letter'

const stageNames: Record<CaseStage, string> = {
  UPLOADED: 'Documents uploaded',
  EXTRACTING: 'Reading documents',
  AWAITING_FACTS: 'Confirm policy facts',
  INVESTIGATING: 'Finding relevant clauses',
  VERIFYING: 'Checking citations and dates',
  READY_FOR_REVIEW: 'Ready for your review',
}

function Icon({ children }: { children: ReactNode }) {
  return <span className="nav-mark" aria-hidden="true">{children}</span>
}

function ShieldIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 19 6v5c0 4.5-3 7.8-7 10-4-2.2-7-5.5-7-10V6l7-3Z" /><path d="m9 12 2 2 4-4" /></svg>
}

function UploadIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5M5 14.5v4A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5v-4" /></svg>
}

function Sidebar({ screen, onNavigate, hasCase }: { screen: Screen; onNavigate: (screen: Screen) => void; hasCase: boolean }) {
  return (
    <aside className="sidebar">
      <a className="brand" href="#home" onClick={(event) => { event.preventDefault(); onNavigate('upload') }} aria-label="ClaimLens home">
        <span className="brand-symbol"><span /></span><span>claim<span className="brand-lens">lens</span></span>
      </a>
      <div className="workspace-label">WORKSPACE</div>
      <nav className="main-nav" aria-label="Main navigation">
        <button className={`nav-link ${screen === 'upload' ? 'nav-active' : ''}`} onClick={() => onNavigate('upload')}><Icon>＋</Icon><span>New review</span></button>
        <button className={`nav-link ${screen === 'case' || screen === 'facts' ? 'nav-active' : ''}`} onClick={() => onNavigate(hasCase ? 'case' : 'upload')}><Icon>▤</Icon><span>My cases</span></button>
        <button className={`nav-link ${screen === 'eval' ? 'nav-active' : ''}`} onClick={() => onNavigate('eval')}><Icon>▦</Icon><span>Evaluation</span></button>
        <a className="nav-link" href="#how-it-works"><Icon>◇</Icon><span>How it works</span></a>
      </nav>
      <div className="sidebar-bottom">
        <div className="privacy-mini"><ShieldIcon /><span>Use synthetic documents only</span></div>
        <div className="profile-row"><div className="avatar">SV</div><div><strong>Shlok Verma</strong><span>Personal workspace</span></div><span className="profile-dots">···</span></div>
      </div>
    </aside>
  )
}

function Header({ crumb, onHelp, theme, onToggleTheme }: { crumb: string; onHelp: () => void; theme: 'light' | 'dark'; onToggleTheme: () => void }) {
  return <div className="topbar"><div className="breadcrumb"><span>Workspace</span><b>/</b><strong>{crumb}</strong></div><div className="topbar-right"><span className="secure-pill"><i /> Mock mode</span><button className="theme-button" type="button" onClick={onToggleTheme} aria-label={`Switch to ${theme === 'light' ? 'dark' : 'light'} mode`} title={`Switch to ${theme === 'light' ? 'dark' : 'light'} mode`}>{theme === 'light' ? '☾' : '☼'}<span>{theme === 'light' ? 'Dark mode' : 'Light mode'}</span></button><button className="help-button" onClick={onHelp}>Need help?</button></div></div>
}

function PipelineTimeline({ stages, running }: { stages: CaseStage[]; running: boolean }) {
  return (
    <section className="pipeline-card" aria-label="Analysis progress">
      <div className="pipeline-heading"><div><span className="step-kicker">CASE PROGRESS</span><h2>{running ? 'Preparing your review' : stages.includes('READY_FOR_REVIEW') ? 'Review ready' : 'Processing steps'}</h2></div><span className="mock-tag"><i /> FIXTURE MODE</span></div>
      <div className="pipeline-list">
        {(['UPLOADED', 'EXTRACTING', 'AWAITING_FACTS', 'INVESTIGATING', 'VERIFYING', 'READY_FOR_REVIEW'] as CaseStage[]).map((stage, index) => {
          const done = stages.includes(stage)
          const current = running && stages.at(-1) === stage
          return <div className={`pipeline-step ${done ? 'is-done' : ''} ${current ? 'is-current' : ''}`} key={stage}><span className="pipeline-dot">{done ? '✓' : index + 1}</span><span>{stageNames[stage]}</span></div>
        })}
      </div>
      <p className="pipeline-footnote">Progress is simulated for this frontend demo; live SSE will connect to M1's API.</p>
    </section>
  )
}

function UploadCard({ kind, fileName, inputRef, onChange, onDrop, onPick }: {
  kind: DocumentKind
  fileName: string | null
  inputRef: React.RefObject<HTMLInputElement | null>
  onChange: (event: ChangeEvent<HTMLInputElement>) => void
  onDrop: (event: DragEvent<HTMLDivElement>) => void
  onPick: () => void
}) {
  const isPolicy = kind === 'policy'
  return (
    <div className={`file-card ${fileName ? 'file-ready' : ''}`} onDragOver={(event) => event.preventDefault()} onDrop={onDrop}>
      <div className="file-card-top"><span className={`file-icon ${isPolicy ? 'policy-icon' : 'letter-icon'}`}>{isPolicy ? 'P' : 'R'}</span><span className="required-label">REQUIRED</span></div>
      <strong>{isPolicy ? 'Health policy' : 'Rejection letter'}</strong>
      <p>{fileName ?? (isPolicy ? 'The document with your coverage and policy terms.' : 'The letter explaining why your claim was rejected.')}</p>
      <button type="button" className="file-select" onClick={onPick}><UploadIcon />{fileName ? 'Replace PDF' : `Choose ${isPolicy ? 'policy' : 'letter'} PDF`}</button>
      <input ref={inputRef} className="visually-hidden" type="file" accept="application/pdf,.pdf" onChange={onChange} aria-label={`Choose ${isPolicy ? 'health policy' : 'rejection letter'} PDF`} />
      <span className="file-hint">PDF only <i /> Max 10 MB</span>
    </div>
  )
}

function UploadPage({ policyName, letterName, policyInput, letterInput, onFile, onDrop, onContinue, onDemo, error, loading }: {
  policyName: string | null
  letterName: string | null
  policyInput: React.RefObject<HTMLInputElement | null>
  letterInput: React.RefObject<HTMLInputElement | null>
  onFile: (kind: DocumentKind, event: ChangeEvent<HTMLInputElement>) => void
  onDrop: (kind: DocumentKind, event: DragEvent<HTMLDivElement>) => void
  onContinue: () => void
  onDemo: () => void
  error: string | null
  loading: boolean
}) {
  return (
    <><section className="intro"><div className="eyebrow"><span className="eyebrow-line" /> HEALTH CLAIM REVIEW</div><h1>Make sense of<br /><span>the fine print.</span></h1><p className="intro-copy">See how your insurer's decision compares with the words in your policy. Clear evidence, side by side.</p></section>
      <section className="workspace-grid" aria-label="Start a claim review"><div className="upload-panel"><div className="panel-heading"><div><span className="step-kicker">LET'S GET STARTED</span><h2>Add your documents</h2></div><span className="step-count">01 <i /> 02</span></div>
        <p className="panel-copy">We will look for the reason for rejection and the matching clause in your policy.</p>
        <div className="file-cards"><UploadCard kind="policy" fileName={policyName} inputRef={policyInput} onChange={(event) => onFile('policy', event)} onDrop={(event) => onDrop('policy', event)} onPick={() => policyInput.current?.click()} /><UploadCard kind="letter" fileName={letterName} inputRef={letterInput} onChange={(event) => onFile('letter', event)} onDrop={(event) => onDrop('letter', event)} onPick={() => letterInput.current?.click()} /></div>
        {error && <div className="upload-error" role="alert">{error}</div>}
        <div className="action-row"><button className="primary-button" type="button" disabled={loading || (!policyName || !letterName)} onClick={onContinue}>{loading ? 'Preparing...' : 'Continue to review'} <span>→</span></button><button className="demo-button" type="button" onClick={onDemo}>Load synthetic demo <span>↗</span></button></div>
        <div className="privacy-note"><ShieldIcon /><span><strong>Demo safety.</strong> Please use synthetic documents only. Do not upload real personal information.</span></div>
      </div>
      <aside className="guide-panel" id="how-it-works"><div className="guide-orb orb-one" /><div className="guide-orb orb-two" /><span className="guide-kicker">A CLEARER WAY FORWARD</span><h2>From rejection<br />to understanding.</h2><p>ClaimLens connects each rejection reason to the exact wording in your policy.</p><div className="journey"><div className="journey-item journey-current"><span className="journey-number">1</span><div><strong>Share your documents</strong><small>Your policy and rejection letter</small></div><span className="journey-check">✓</span></div><div className="journey-item"><span className="journey-number">2</span><div><strong>We find the evidence</strong><small>Reasons matched with policy clauses</small></div></div><div className="journey-item"><span className="journey-number">3</span><div><strong>You stay in control</strong><small>Review every finding yourself</small></div></div></div><div className="guide-footer"><span className="guide-shield"><ShieldIcon /></span><span>Evidence you can check.<br /><strong>Decisions that stay yours.</strong></span></div></aside></section>
      <footer className="page-footer"><span>ClaimLens is an evidence tool, not legal advice.</span><span>Built for clarity <b>✳</b></span></footer></>
  )
}

function FactsPage({ facts, onChange, onConfirm, onBack, stages, running }: { facts: PolicyFacts; onChange: (facts: PolicyFacts) => void; onConfirm: () => void; onBack: () => void; stages: CaseStage[]; running: boolean }) {
  const valid = Boolean(facts.policy_start && facts.policy_end && facts.waiting_period_days >= 0 && facts.waiting_period_days <= 3650 && facts.policy_end >= facts.policy_start)
  return <div className="flow-page"><button className="back-button" onClick={onBack}>← Back to documents</button><div className="flow-heading"><span className="eyebrow"><span className="eyebrow-line" /> HUMAN REVIEW STEP</span><h1>Confirm policy facts</h1><p>Check the extracted details before the sample analysis continues. You can correct them here.</p></div><div className="flow-grid"><section className="upload-panel facts-panel"><div className="panel-heading"><div><span className="step-kicker">EXTRACTED FROM POLICY</span><h2>Policy details</h2></div><span className="mock-tag"><i /> MOCK DATA</span></div><label className="field-label">Policy start date<input type="date" value={facts.policy_start} onChange={(event) => onChange({ ...facts, policy_start: event.target.value })} /></label><label className="field-label">Policy end date<input type="date" value={facts.policy_end} onChange={(event) => onChange({ ...facts, policy_end: event.target.value })} /></label><label className="field-label">Initial waiting period (days)<input type="number" min="0" max="3650" value={facts.waiting_period_days} onChange={(event) => onChange({ ...facts, waiting_period_days: Number(event.target.value) })} /></label><p className="field-help">These fixture values can be edited for the UI demo. Real extraction is not connected.</p><button className="primary-button" disabled={!valid || running} onClick={onConfirm}>{running ? 'Running sample stages...' : 'Confirm facts and continue'} <span>→</span></button></section><PipelineTimeline stages={stages} running={running} /></div></div>
}

function EvidencePane({ kind, finding }: { kind: DocumentKind; finding: Finding }) {
  const citation = finding.citations.find((item) => item.document === kind)
  const policyQuote = 'Illnesses (other than injuries caused by an accident) that first occur during the first 30 days from the policy start date are not covered. This waiting period does not apply after the first 30 days have elapsed.'
  const letterQuote = 'Your reimbursement claim is declined because the treatment took place during the initial 30-day waiting period under the policy.'
  const quote = kind === 'policy' ? policyQuote : letterQuote
  const highlight = kind === 'policy' ? 'first 30 days from the policy start date' : 'initial 30-day waiting period'
  const [before, after = ''] = quote.split(highlight)
  return <article className="evidence-card"><div className="evidence-card-head"><span className={`file-icon ${kind === 'policy' ? 'policy-icon' : 'letter-icon'}`}>{kind === 'policy' ? 'P' : 'R'}</span><div><strong>{kind === 'policy' ? 'Health policy' : 'Rejection letter'}</strong><small>{citation?.section_path ?? 'Page 1'} · Page {citation?.page ?? 1}</small></div></div><div className="document-paper"><span className="paper-label">{kind === 'policy' ? 'CLAUSE 3.1 / INITIAL WAITING PERIOD' : 'CLAIM DECISION NOTICE / REJECTION REASON'}</span><p>{before}<mark>{highlight}</mark>{after}</p><span className="mock-quote-label">MOCK TEXT EXCERPT - NOT A PDF VIEWER</span></div></article>
}

function ReviewPanel({ status, onReview, editText, onEditText, onSaveEdit, auditLog, finding }: { status: string; onReview: (action: ReviewAction) => void; editText: string; onEditText: (text: string) => void; onSaveEdit: () => void; auditLog: ReviewLogEntry[]; finding: Finding }) {
  const [showChallenge, setShowChallenge] = useState(true)
  const [editing, setEditing] = useState(false)
  return <div className="review-panel-grid"><section className="review-box"><div className="box-heading"><div><span className="step-kicker">VERIFICATION</span><h2>Verifier challenge log</h2></div><span className="status-chip">{finding.status.replaceAll('_', ' ')}</span></div><button className="challenge-toggle" onClick={() => setShowChallenge((visible) => !visible)}>{showChallenge ? 'Hide' : 'Show'} challenge details <span>{showChallenge ? '−' : '+'}</span></button>{showChallenge && <div className="challenge-content"><div className="challenge-row"><span className="challenge-label">ADVERSARY</span><p>{finding.attacker_argument}</p></div><div className="challenge-row"><span className="challenge-label">REBUTTAL</span><p>{finding.rebuttal}</p></div><div className="check-row"><span><b className="check-ok">✓</b> Citation quote found in fixture</span><span><b className="check-note">!</b> Onset date needs human confirmation</span></div><p className="mock-data-note">Mock verifier output - not a real AI check.</p></div>}</section>
    <section className="review-box"><div className="box-heading"><div><span className="step-kicker">YOUR DECISION</span><h2>Review this finding</h2></div><span className={`review-state state-${status.toLowerCase()}`}>{status}</span></div><p className="review-guidance">Please check the cited text and facts. Your action is saved in this browser demo only.</p>{editing ? <div className="edit-area"><label className="field-label">Your edited note<textarea value={editText} onChange={(event) => onEditText(event.target.value)} rows={3} /></label><div className="action-row"><button className="primary-button" onClick={() => { onSaveEdit(); setEditing(false) }}>Save edit</button><button className="demo-button" onClick={() => setEditing(false)}>Cancel</button></div></div> : <div className="review-actions"><button className="decision-button approve" onClick={() => onReview('APPROVE')}>Approve</button><button className="decision-button reject" onClick={() => onReview('REJECT')}>Reject</button><button className="decision-button edit" onClick={() => { setEditing(true); onEditText(finding.reasoning) }}>Edit</button></div>}{status === 'APPROVED' && <DraftEditor />}</section>
    <section className="review-box audit-box"><div className="box-heading"><div><span className="step-kicker">AUDIT TRAIL</span><h2>Review actions</h2></div><span className="step-count">{auditLog.length} saved</span></div>{auditLog.length ? <ol className="audit-list">{[...auditLog].reverse().map((entry, index) => <li key={`${entry.timestamp}-${index}`}><b>{entry.action}</b><span>{entry.note || 'Finding review action recorded.'}</span><time>{new Date(entry.timestamp).toLocaleTimeString()}</time></li>)}</ol> : <p className="empty-audit">No review action yet. Approve, reject or edit the finding to record one.</p>}</section></div>
}

function DraftEditor() {
  const [open, setOpen] = useState(false)
  return <div className="draft-block"><button className="draft-toggle" onClick={() => setOpen((value) => !value)}>{open ? 'Hide draft' : 'Create review-request draft'} <span>↗</span></button>{open && <div className="draft-preview"><span className="step-kicker">DRAFT PREVIEW - APPROVED FINDING ONLY</span><p>Dear Claims Team,</p><p>Please review the stated waiting-period ground for claim DEMO-CLAIM-104. The attached sample policy states that the initial waiting period is 30 days from the policy start date (Clause 3.1). The sample letter lists admission on 25 May 2026, while the policy start date is 01 April 2026.</p><p className="citation-chip">[Sample policy, Clause 3.1, page 1]</p><p>This is a mock draft for UI testing. Please verify all details before use.</p></div>}</div>
}

function CasePage({ caseData, facts, stages, running, reviewStatus, onReview, editText, onEditText, onSaveEdit, auditLog, onBackToFacts }: {
  caseData: CaseFixture
  facts: PolicyFacts
  stages: CaseStage[]
  running: boolean
  reviewStatus: string
  onReview: (action: ReviewAction) => void
  editText: string
  onEditText: (text: string) => void
  onSaveEdit: () => void
  auditLog: ReviewLogEntry[]
  onBackToFacts: () => void
}) {
  const finding = caseData.findings[0]
  const days = Math.floor((new Date(caseData.admission_date).getTime() - new Date(facts.policy_start).getTime()) / 86400000)
  return <div className="case-page"><div className="case-heading"><div><button className="back-button" onClick={onBackToFacts}>← Confirmed facts</button><h1>Evidence review</h1><p>Case {caseData.case_id} · Synthetic sample</p></div><span className="mock-tag"><i /> FIXTURE PREVIEW</span></div><PipelineTimeline stages={stages} running={running} /><section className="facts-strip"><div><span>POLICY START</span><strong>{facts.policy_start}</strong></div><div><span>ADMISSION DATE</span><strong>{caseData.admission_date}</strong></div><div><span>WAITING PERIOD</span><strong>{facts.waiting_period_days} days</strong></div><div className="facts-rule"><span>DATE CHECK</span><strong>{days} days after start (fixture)</strong></div></section><section className="finding-summary"><div className="finding-icon">1</div><div className="finding-copy"><span className="step-kicker">REJECTION GROUND</span><h2>{finding.reason}</h2><p>{finding.assessment.replaceAll('_', ' ')} - cited clauses do not appear to support this ground in the sample facts.</p></div><span className="assessment-pill">{finding.assessment.replaceAll('_', ' ')} <small>MOCK</small></span></section><div className="evidence-grid"><EvidencePane kind="letter" finding={finding} /><EvidencePane kind="policy" finding={finding} /></div><div className="evidence-footnote"><ShieldIcon /><span>Highlighted text comes from the sample fixture. Actual PDF page and bounding-box highlighting awaits M4's document coordinates.</span></div><ReviewPanel status={reviewStatus} onReview={onReview} editText={editText} onEditText={onEditText} onSaveEdit={onSaveEdit} auditLog={auditLog} finding={finding} /></div>
}

function EvalPage() {
  return <div className="flow-page"><div className="flow-heading"><span className="eyebrow"><span className="eyebrow-line" /> EVALUATION</span><h1>Measure the demo</h1><p>Metrics from M4's synthetic evaluation report will appear here when the API endpoint is available.</p></div><section className="upload-panel eval-placeholder"><span className="mock-tag"><i /> REPORT NOT CONNECTED</span><h2>No evaluation report loaded</h2><p>This view is ready for the latest report.json. We will show exact counts from the synthetic cases once M4 publishes the fixture or endpoint.</p><div className="eval-metric-grid"><div><span>Extraction labels</span><b>Waiting for report</b></div><div><span>Retrieval recall@3</span><b>Waiting for report</b></div><div><span>Citation grounding</span><b>Waiting for report</b></div><div><span>Verifier injected-fault catch</span><b>Waiting for report</b></div></div></section></div>
}

function App() {
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    try { return window.localStorage.getItem('claimlens-theme') === 'dark' ? 'dark' : 'light' } catch { return 'light' }
  })
  const [screen, setScreen] = useState<Screen>('upload')
  const [policy, setPolicy] = useState<File | null>(null)
  const [letter, setLetter] = useState<File | null>(null)
  const [demoLoaded, setDemoLoaded] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [caseData, setCaseData] = useState<CaseFixture>(demoCase)
  const [facts, setFacts] = useState<PolicyFacts>(demoCase.facts)
  const [reviewStatus, setReviewStatus] = useState('PENDING')
  const [auditLog, setAuditLog] = useState<ReviewLogEntry[]>([])
  const [editText, setEditText] = useState('')
  const policyInput = useRef<HTMLInputElement>(null)
  const letterInput = useRef<HTMLInputElement>(null)
  const [caseStarted, setCaseStarted] = useState(false)
  const pipeline = useCaseEvents()

  // Live SSE from the real backend (only set after a real upload)
  const [liveCaseId, setLiveCaseId] = useState<string | null>(null)
  const live = useLiveCaseEvents(liveCaseId)

  useEffect(() => {
    if (live.events.length) console.log('LIVE SSE events', live.events)
  }, [live.events])

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try { window.localStorage.setItem('claimlens-theme', theme) } catch { /* Theme still works for this session. */ }
  }, [theme])

  const selectFile = (kind: DocumentKind, file?: File) => {
    if (!file) return
    const validation = validatePdf(file)
    if (validation) { setError(validation); return }
    setError(null)
    setDemoLoaded(false)
    if (kind === 'policy') setPolicy(file)
    else setLetter(file)
  }

  const onFile = (kind: DocumentKind, event: ChangeEvent<HTMLInputElement>) => selectFile(kind, event.target.files?.[0])
  const onDrop = (kind: DocumentKind, event: DragEvent<HTMLDivElement>) => { event.preventDefault(); selectFile(kind, event.dataTransfer.files[0]) }
  const policyName = demoLoaded ? demoCase.policy_file : policy?.name ?? null
  const letterName = demoLoaded ? demoCase.letter_file : letter?.name ?? null

  const startReview = async () => {
    if (!policyName || !letterName) return
    setLoading(true)
    setError(null)
    try {
      let realCaseId: string | null = null
      if (!demoLoaded && policy && letter) {
        const result = await uploadCase(policy, letter)
        realCaseId = result.case_id
        setLiveCaseId(result.case_id)
      } else {
        setLiveCaseId(null)
      }
      const nextCase = await caseApi.getDemoCase()
      setCaseData(realCaseId ? { ...nextCase, case_id: realCaseId } : nextCase)
      setFacts(nextCase.facts)
      setReviewStatus('PENDING')
      setAuditLog([])
      setCaseStarted(true)
      setScreen('facts')
      await pipeline.extract()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  const confirmFacts = async () => {
    setFacts((current) => ({ ...current, confirmed_by_user: true }))
    setScreen('case')
    await pipeline.analyze()
  }

  const recordReview = async (action: ReviewAction, note = '') => {
    const entry = await caseApi.saveReviewAction(caseData.findings[0].finding_id, action, note)
    setAuditLog((current) => [...current, entry])
    setReviewStatus(action === 'EDIT' ? 'EDITED' : action === 'APPROVE' ? 'APPROVED' : 'REJECTED')
  }

  const saveEdit = () => {
    if (!editText.trim()) { setError('Add a short note before saving your edit.'); return }
    setError(null)
    setCaseData((current) => ({ ...current, findings: current.findings.map((finding) => ({ ...finding, reasoning: editText })) }))
    void recordReview('EDIT', editText)
  }

  const loadDemo = () => {
    setError(null)
    setPolicy(null)
    setLetter(null)
    setDemoLoaded(true)
  }

  const showHelp = () => document.getElementById('how-it-works')?.scrollIntoView({ behavior: 'smooth' })
  const reviewAction = (action: ReviewAction) => { setError(null); void recordReview(action) }

  return <div className="app-shell"><Sidebar screen={screen} hasCase={caseStarted} onNavigate={(next) => setScreen(next)} /><main className="main-area"><Header crumb={screen === 'upload' ? 'New review' : screen === 'facts' ? 'Confirm facts' : screen === 'case' ? 'Evidence review' : 'Evaluation'} onHelp={showHelp} theme={theme} onToggleTheme={() => setTheme((current) => current === 'light' ? 'dark' : 'light')} /><div className={`content-wrap ${screen === 'case' ? 'case-content' : ''}`}>
    {screen === 'upload' && <UploadPage policyName={policyName} letterName={letterName} policyInput={policyInput} letterInput={letterInput} onFile={onFile} onDrop={onDrop} onContinue={() => void startReview()} onDemo={loadDemo} error={error} loading={loading} />}
    {screen === 'facts' && <FactsPage facts={facts} onChange={setFacts} onConfirm={() => void confirmFacts()} onBack={() => setScreen('upload')} stages={pipeline.stages} running={pipeline.running} />}
    {screen === 'case' && <CasePage caseData={caseData} facts={facts} stages={pipeline.stages} running={pipeline.running} reviewStatus={reviewStatus} onReview={reviewAction} editText={editText} onEditText={setEditText} onSaveEdit={saveEdit} auditLog={auditLog} onBackToFacts={() => setScreen('facts')} />}
    {screen === 'eval' && <EvalPage />}
  </div></main></div>
}

export default App