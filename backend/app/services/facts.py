import logging
from typing import Optional
from sqlmodel import Session, select

import backend.app.db.session as db_session
from backend.app.db.models import Case
from contracts.schemas import PolicyFacts

logger = logging.getLogger("claimlens.facts")


def save_facts(case_id: str, facts: PolicyFacts, confirmed: bool = False) -> PolicyFacts:
    """Validate and persist PolicyFacts JSON to the Case record using a short-lived Session."""
    validated_facts = PolicyFacts.model_validate(facts)
    if confirmed:
        validated_facts.confirmed_by_user = True

    facts_json_str = validated_facts.model_dump_json()

    with Session(db_session.engine) as session:
        case = session.exec(select(Case).where(Case.case_id == case_id)).one_or_none()
        if case:
            case.facts_json = facts_json_str
            if confirmed:
                case.facts_confirmed = True
            session.add(case)
            session.commit()

    return validated_facts


def load_facts(case_id: str) -> Optional[PolicyFacts]:
    """Retrieve and parse PolicyFacts JSON from the Case record using a short-lived Session."""
    with Session(db_session.engine) as session:
        case = session.exec(select(Case).where(Case.case_id == case_id)).one_or_none()
        if not case or not case.facts_json:
            return None
        return PolicyFacts.model_validate_json(case.facts_json)
