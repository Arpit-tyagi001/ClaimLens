export type Assessment = 'SUPPORTED' | 'NOT_SUPPORTED' | 'PARTIAL' | 'INSUFFICIENT_EVIDENCE'
export type VerificationStatus = 'VERIFIED' | 'DOWNGRADED' | 'NEEDS_HUMAN' | 'REJECTED_UNGROUNDED'
export type CaseStage = 'UPLOADED' | 'EXTRACTING' | 'AWAITING_FACTS' | 'INVESTIGATING' | 'VERIFYING' | 'READY_FOR_REVIEW'
export type ReviewAction = 'APPROVE' | 'REJECT' | 'EDIT'

export interface PolicyFacts {
  policy_start: string
  policy_end: string
  waiting_period_days: number
  confirmed_by_user: boolean
}

export interface Citation {
  document: 'policy' | 'letter'
  page: number
  section_path: string
  quote: string
  bbox?: [number, number, number, number]
}

export interface Finding {
  finding_id: string
  reason: string
  assessment: Assessment
  confidence: number
  reasoning: string
  status: VerificationStatus
  citations: Citation[]
  attacker_argument: string
  rebuttal: string
  citation_found: boolean
  rule_check: string
}

export interface CaseFixture {
  case_id: string
  case_name: string
  policy_file: string
  letter_file: string
  facts: PolicyFacts
  admission_date: string
  findings: Finding[]
  stages: CaseStage[]
}

export interface ReviewLogEntry {
  action: ReviewAction
  finding_id: string
  note: string
  timestamp: string
}
