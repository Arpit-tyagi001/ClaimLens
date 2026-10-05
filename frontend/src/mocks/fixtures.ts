import type { CaseFixture } from '../api/types'

export const demoCase: CaseFixture = {
  case_id: 'demo-claim-104',
  case_name: 'Waiting period sample',
  policy_file: 'ClaimLens_Synthetic_Sample_Health_Policy.pdf',
  letter_file: 'ClaimLens_Synthetic_Sample_Rejection_Letter.pdf',
  facts: {
    policy_start: '2026-04-01',
    policy_end: '2027-03-31',
    waiting_period_days: 30,
    confirmed_by_user: false,
  },
  admission_date: '2026-05-25',
  stages: ['UPLOADED', 'EXTRACTING', 'AWAITING_FACTS', 'INVESTIGATING', 'VERIFYING', 'READY_FOR_REVIEW'],
  findings: [
    {
      finding_id: 'finding-waiting-period-1',
      reason: 'Initial waiting period',
      assessment: 'NOT_SUPPORTED',
      confidence: 0.72,
      reasoning: 'The synthetic letter says the admission took place during the initial 30-day period. The supplied sample facts list admission on 25 May 2026, while the policy starts on 01 April 2026. That date is outside the first 30 days, so the stated ground appears inconsistent with these sample facts.',
      status: 'VERIFIED',
      citations: [
        {
          document: 'letter',
          page: 1,
          section_path: 'Reason for rejection',
          quote: 'Your reimbursement claim is declined because the treatment took place during the initial 30-day waiting period under the policy.',
        },
        {
          document: 'policy',
          page: 1,
          section_path: '3. Waiting periods > Clause 3.1',
          quote: 'Illnesses (other than injuries caused by an accident) that first occur during the first 30 days from the policy start date are not covered. This waiting period does not apply after the first 30 days have elapsed.',
          bbox: [50, 150, 520, 230],
        },
      ],
      attacker_argument: 'The policy clause applies to when an illness first occurs, while the letter refers to treatment. The supplied documents do not independently establish when the illness first began.',
      rebuttal: 'The sample rejection letter itself states that treatment was within the initial period, but the listed admission date is 25 May 2026. The date comparison conflicts with that stated reason; the illness onset still needs human confirmation.',
      citation_found: true,
      rule_check: 'Admission date is more than 30 days after the policy start date.',
    },
  ],
}

export const evalSummary = {
  status: 'Sample report not connected',
  note: 'M4 evaluation metrics will appear here when the backend serves the report.',
}
