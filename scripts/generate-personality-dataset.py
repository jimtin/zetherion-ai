#!/usr/bin/env python3
"""Generate a synthetic conversation dataset for personality extraction benchmarking.

Produces realistic multi-message conversation threads between a fixed owner
persona and ~20 contact personas with distinct, defined personality traits.
Each message includes ground truth personality labels for benchmark scoring.

Usage:
    python scripts/generate-personality-dataset.py --seed 42
    python scripts/generate-personality-dataset.py --threads-per-persona 3
"""

import argparse
import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Owner persona (fixed)
# ---------------------------------------------------------------------------

OWNER_PERSONA = {
    "name": "James Hinton",
    "email": "james@zetherion.com",
    "alt_emails": ["james.hinton@gmail.com"],
    "role": "Software Engineer & Founder",
    "company": "Zetherion",
    "traits": {
        "formality": "semi_formal",
        "primary_trait": "direct",
        "secondary_trait": "analytical",
        "emotional_tone": "warm",
        "assertiveness": 0.65,
        "avg_sentence_length": "medium",
        "uses_emoji": False,
        "uses_bullet_points": True,
        "vocabulary_level": "technical",
    },
    "greeting_templates": ["Hi {name},", "Hey {name},", "{name},"],
    "signoff_templates": ["Cheers,\nJames", "Thanks,\nJames", "Best,\nJames"],
    "preferences": [
        "prefers async communication",
        "morning worker",
        "values concise messages",
    ],
}

# ---------------------------------------------------------------------------
# Contact personas (~20)
# ---------------------------------------------------------------------------

CONTACT_PERSONAS = {
    "sarah_chen": {
        "name": "Sarah Chen",
        "emails": ["sarah.chen@acmecorp.com", "s.chen@gmail.com"],
        "role": "Engineering Manager",
        "company": "Acme Corp",
        "relationship_to_owner": "manager",
        "traits": {
            "formality": "formal",
            "primary_trait": "diplomatic",
            "secondary_trait": "analytical",
            "emotional_tone": "warm",
            "assertiveness": 0.7,
            "avg_sentence_length": "medium",
            "uses_emoji": False,
            "uses_bullet_points": True,
            "vocabulary_level": "standard",
        },
        "greeting_templates": ["Hi James,", "Hello James,", "Good morning James,"],
        "signoff_templates": [
            "Best regards,\nSarah",
            "Kind regards,\nSarah Chen",
            "Best,\nSarah",
        ],
        "power_dynamic": "superior",
        "familiarity": 0.7,
        "trust_level": 0.75,
    },
    "tom_wilson": {
        "name": "Tom Wilson",
        "emails": ["tom.wilson@gmail.com"],
        "role": "Friend",
        "company": "",
        "relationship_to_owner": "friend",
        "traits": {
            "formality": "very_casual",
            "primary_trait": "emotional",
            "secondary_trait": None,
            "emotional_tone": "enthusiastic",
            "assertiveness": 0.4,
            "avg_sentence_length": "short",
            "uses_emoji": True,
            "uses_bullet_points": False,
            "vocabulary_level": "simple",
        },
        "greeting_templates": ["mate!", "dude!", "yo!", ""],
        "signoff_templates": ["", "later! 🍻", "cheers mate"],
        "power_dynamic": "peer",
        "familiarity": 0.9,
        "trust_level": 0.9,
    },
    "emily_brooks": {
        "name": "Emily Brooks",
        "emails": ["legal@clientco.com", "emily.brooks@clientco.com"],
        "role": "Legal Counsel",
        "company": "ClientCo",
        "relationship_to_owner": "client",
        "traits": {
            "formality": "very_formal",
            "primary_trait": "analytical",
            "secondary_trait": "diplomatic",
            "emotional_tone": "reserved",
            "assertiveness": 0.8,
            "avg_sentence_length": "long",
            "uses_emoji": False,
            "uses_bullet_points": True,
            "vocabulary_level": "academic",
        },
        "greeting_templates": [
            "Dear James,",
            "Dear Mr. Hinton,",
            "Good afternoon James,",
        ],
        "signoff_templates": [
            "Kind regards,\nEmily Brooks\nLegal Counsel, ClientCo",
            "Best regards,\nEmily Brooks",
            "Regards,\nEmily Brooks\nLegal Department",
        ],
        "power_dynamic": "client",
        "familiarity": 0.3,
        "trust_level": 0.5,
    },
    "mum": {
        "name": "Mum",
        "emails": ["mum@gmail.com"],
        "role": "Mum",
        "company": "",
        "relationship_to_owner": "family",
        "traits": {
            "formality": "very_casual",
            "primary_trait": "emotional",
            "secondary_trait": None,
            "emotional_tone": "warm",
            "assertiveness": 0.5,
            "avg_sentence_length": "short",
            "uses_emoji": True,
            "uses_bullet_points": False,
            "vocabulary_level": "simple",
        },
        "greeting_templates": ["Hi love,", "Hello darling,", "Hi sweetheart,", "James,"],
        "signoff_templates": [
            "Love, Mum xx",
            "Mum xxx",
            "Love you, Mum x",
            "Mum 💕",
        ],
        "power_dynamic": "superior",
        "familiarity": 1.0,
        "trust_level": 1.0,
    },
    "ahmed_khan": {
        "name": "Ahmed Khan",
        "emails": ["ahmed.khan@techpartners.io"],
        "role": "CTO",
        "company": "Tech Partners",
        "relationship_to_owner": "colleague",
        "traits": {
            "formality": "semi_formal",
            "primary_trait": "direct",
            "secondary_trait": "terse",
            "emotional_tone": "neutral",
            "assertiveness": 0.75,
            "avg_sentence_length": "short",
            "uses_emoji": False,
            "uses_bullet_points": True,
            "vocabulary_level": "technical",
        },
        "greeting_templates": ["James,", "Hey James,", "Hi,"],
        "signoff_templates": ["— Ahmed", "Ahmed", "AK"],
        "power_dynamic": "peer",
        "familiarity": 0.6,
        "trust_level": 0.7,
    },
    "david_martinez": {
        "name": "David Martinez",
        "emails": ["david.martinez@clientco.com"],
        "role": "Head of Technology",
        "company": "ClientCo",
        "relationship_to_owner": "client",
        "traits": {
            "formality": "semi_formal",
            "primary_trait": "diplomatic",
            "secondary_trait": "verbose",
            "emotional_tone": "warm",
            "assertiveness": 0.6,
            "avg_sentence_length": "long",
            "uses_emoji": False,
            "uses_bullet_points": False,
            "vocabulary_level": "standard",
        },
        "greeting_templates": ["Hi James,", "Hello James,", "James,"],
        "signoff_templates": [
            "Thanks,\nDavid",
            "Best,\nDavid Martinez",
            "Cheers,\nDavid",
        ],
        "power_dynamic": "client",
        "familiarity": 0.6,
        "trust_level": 0.65,
    },
    "rachel_design": {
        "name": "Rachel",
        "emails": ["vendor.design@creativestudio.io"],
        "role": "Designer",
        "company": "Creative Studio",
        "relationship_to_owner": "vendor",
        "traits": {
            "formality": "casual",
            "primary_trait": "emotional",
            "secondary_trait": "verbose",
            "emotional_tone": "enthusiastic",
            "assertiveness": 0.45,
            "avg_sentence_length": "medium",
            "uses_emoji": True,
            "uses_bullet_points": False,
            "vocabulary_level": "standard",
        },
        "greeting_templates": ["Hey James! 🎨", "Hi James!", "Hey!"],
        "signoff_templates": [
            "Rachel ✨",
            "Talk soon!\nRachel",
            "— Rachel @ Creative Studio",
        ],
        "power_dynamic": "vendor",
        "familiarity": 0.5,
        "trust_level": 0.6,
    },
    "alex_rivera": {
        "name": "Alex Rivera",
        "emails": ["ceo@startupxyz.com"],
        "role": "CEO",
        "company": "StartupXYZ",
        "relationship_to_owner": "client",
        "traits": {
            "formality": "casual",
            "primary_trait": "direct",
            "secondary_trait": "emotional",
            "emotional_tone": "enthusiastic",
            "assertiveness": 0.85,
            "avg_sentence_length": "short",
            "uses_emoji": False,
            "uses_bullet_points": True,
            "vocabulary_level": "standard",
        },
        "greeting_templates": ["James!", "Hey James,", "James —"],
        "signoff_templates": ["— Alex", "Alex", "AR"],
        "power_dynamic": "client",
        "familiarity": 0.55,
        "trust_level": 0.6,
    },
    "robert_chen": {
        "name": "Robert Chen",
        "emails": ["ceo@acmecorp.com"],
        "role": "CEO",
        "company": "Acme Corp",
        "relationship_to_owner": "manager",
        "traits": {
            "formality": "formal",
            "primary_trait": "direct",
            "secondary_trait": "diplomatic",
            "emotional_tone": "neutral",
            "assertiveness": 0.9,
            "avg_sentence_length": "medium",
            "uses_emoji": False,
            "uses_bullet_points": True,
            "vocabulary_level": "standard",
        },
        "greeting_templates": ["James,", "Hi James,", "Team,"],
        "signoff_templates": [
            "Robert Chen\nCEO, Acme Corp",
            "Best,\nRobert",
            "RC",
        ],
        "power_dynamic": "superior",
        "familiarity": 0.4,
        "trust_level": 0.55,
    },
    "sophie_williams": {
        "name": "Sophie Williams",
        "emails": ["sophie@freelancedev.co.uk"],
        "role": "Freelancer",
        "company": "",
        "relationship_to_owner": "colleague",
        "traits": {
            "formality": "casual",
            "primary_trait": "diplomatic",
            "secondary_trait": None,
            "emotional_tone": "warm",
            "assertiveness": 0.4,
            "avg_sentence_length": "medium",
            "uses_emoji": True,
            "uses_bullet_points": False,
            "vocabulary_level": "standard",
        },
        "greeting_templates": ["Hey James 👋", "Hi James!", "Hiya,"],
        "signoff_templates": [
            "Sophie 😊",
            "Thanks!\nSophie",
            "Soph",
        ],
        "power_dynamic": "peer",
        "familiarity": 0.55,
        "trust_level": 0.6,
    },
    "carlos_rodriguez": {
        "name": "Carlos Rodriguez",
        "emails": ["carlos@investmentfirm.com"],
        "role": "Account Manager",
        "company": "InvestCo",
        "relationship_to_owner": "vendor",
        "traits": {
            "formality": "formal",
            "primary_trait": "diplomatic",
            "secondary_trait": "verbose",
            "emotional_tone": "warm",
            "assertiveness": 0.55,
            "avg_sentence_length": "long",
            "uses_emoji": False,
            "uses_bullet_points": True,
            "vocabulary_level": "standard",
        },
        "greeting_templates": [
            "Dear James,",
            "Good morning James,",
            "Hi James,",
        ],
        "signoff_templates": [
            "Warm regards,\nCarlos Rodriguez\nAccount Manager, InvestCo",
            "Kind regards,\nCarlos",
            "Best regards,\nCarlos Rodriguez",
        ],
        "power_dynamic": "vendor",
        "familiarity": 0.35,
        "trust_level": 0.5,
    },
    "priya_sharma": {
        "name": "Priya Sharma",
        "emails": ["priya.sharma@acmecorp.com"],
        "role": "QA Lead",
        "company": "Acme Corp",
        "relationship_to_owner": "colleague",
        "traits": {
            "formality": "semi_formal",
            "primary_trait": "analytical",
            "secondary_trait": "direct",
            "emotional_tone": "neutral",
            "assertiveness": 0.6,
            "avg_sentence_length": "medium",
            "uses_emoji": False,
            "uses_bullet_points": True,
            "vocabulary_level": "technical",
        },
        "greeting_templates": ["Hi James,", "James,", "Hey James,"],
        "signoff_templates": ["Priya", "Thanks,\nPriya", "— Priya S."],
        "power_dynamic": "peer",
        "familiarity": 0.6,
        "trust_level": 0.7,
    },
    "jane_hinton": {
        "name": "Jane Hinton",
        "emails": ["sister.jane@gmail.com"],
        "role": "Sister",
        "company": "",
        "relationship_to_owner": "family",
        "traits": {
            "formality": "very_casual",
            "primary_trait": "direct",
            "secondary_trait": "emotional",
            "emotional_tone": "warm",
            "assertiveness": 0.55,
            "avg_sentence_length": "short",
            "uses_emoji": True,
            "uses_bullet_points": False,
            "vocabulary_level": "simple",
        },
        "greeting_templates": ["Hey bro,", "James!", "Oi,", ""],
        "signoff_templates": ["Jane x", "J x", "xxx", ""],
        "power_dynamic": "peer",
        "familiarity": 0.95,
        "trust_level": 0.95,
    },
    "mark_hinton": {
        "name": "Mark Hinton",
        "emails": ["brother.mark@gmail.com"],
        "role": "Brother",
        "company": "",
        "relationship_to_owner": "family",
        "traits": {
            "formality": "very_casual",
            "primary_trait": "terse",
            "secondary_trait": None,
            "emotional_tone": "neutral",
            "assertiveness": 0.5,
            "avg_sentence_length": "short",
            "uses_emoji": False,
            "uses_bullet_points": False,
            "vocabulary_level": "simple",
        },
        "greeting_templates": ["", "James", "Bro"],
        "signoff_templates": ["", "Mark", "M"],
        "power_dynamic": "peer",
        "familiarity": 0.95,
        "trust_level": 0.9,
    },
    "dad": {
        "name": "Dad",
        "emails": ["dad@outlook.com"],
        "role": "Dad",
        "company": "",
        "relationship_to_owner": "family",
        "traits": {
            "formality": "casual",
            "primary_trait": "direct",
            "secondary_trait": None,
            "emotional_tone": "warm",
            "assertiveness": 0.6,
            "avg_sentence_length": "medium",
            "uses_emoji": False,
            "uses_bullet_points": False,
            "vocabulary_level": "simple",
        },
        "greeting_templates": ["Hi James,", "Son,", "James,", "Hello James,"],
        "signoff_templates": ["Dad", "Love Dad", "Dad x"],
        "power_dynamic": "superior",
        "familiarity": 1.0,
        "trust_level": 1.0,
    },
    "nadia_petrova": {
        "name": "Nadia Petrova",
        "emails": ["nadia@designagency.com"],
        "role": "Creative Director",
        "company": "Design Agency",
        "relationship_to_owner": "vendor",
        "traits": {
            "formality": "semi_formal",
            "primary_trait": "diplomatic",
            "secondary_trait": "emotional",
            "emotional_tone": "enthusiastic",
            "assertiveness": 0.65,
            "avg_sentence_length": "medium",
            "uses_emoji": True,
            "uses_bullet_points": False,
            "vocabulary_level": "standard",
        },
        "greeting_templates": ["Hi James!", "Hey James 🙌", "Hello!"],
        "signoff_templates": [
            "Nadia ✨",
            "Best,\nNadia Petrova",
            "— Nadia",
        ],
        "power_dynamic": "vendor",
        "familiarity": 0.5,
        "trust_level": 0.55,
    },
    "mike_johnson": {
        "name": "Mike Johnson",
        "emails": ["mike.johnson@acmecorp.com"],
        "role": "Backend Engineer",
        "company": "Acme Corp",
        "relationship_to_owner": "colleague",
        "traits": {
            "formality": "casual",
            "primary_trait": "terse",
            "secondary_trait": "analytical",
            "emotional_tone": "neutral",
            "assertiveness": 0.5,
            "avg_sentence_length": "short",
            "uses_emoji": False,
            "uses_bullet_points": True,
            "vocabulary_level": "technical",
        },
        "greeting_templates": ["James,", "Hey,", ""],
        "signoff_templates": ["Mike", "— Mike", "MJ"],
        "power_dynamic": "peer",
        "familiarity": 0.65,
        "trust_level": 0.7,
    },
    "lisa_patel": {
        "name": "Lisa Patel",
        "emails": ["lisa.patel@acmecorp.com"],
        "role": "Frontend Engineer",
        "company": "Acme Corp",
        "relationship_to_owner": "colleague",
        "traits": {
            "formality": "casual",
            "primary_trait": "verbose",
            "secondary_trait": "diplomatic",
            "emotional_tone": "warm",
            "assertiveness": 0.45,
            "avg_sentence_length": "long",
            "uses_emoji": True,
            "uses_bullet_points": False,
            "vocabulary_level": "technical",
        },
        "greeting_templates": ["Hi James! 😊", "Hey James,", "Morning!"],
        "signoff_templates": [
            "Lisa 🙂",
            "Thanks James!\nLisa",
            "— Lisa P.",
        ],
        "power_dynamic": "peer",
        "familiarity": 0.65,
        "trust_level": 0.7,
    },
    "recruiter_james_carter": {
        "name": "James Carter",
        "emails": ["james.carter@talentscout.io"],
        "role": "Senior Recruiter",
        "company": "TalentScout",
        "relationship_to_owner": "acquaintance",
        "traits": {
            "formality": "formal",
            "primary_trait": "diplomatic",
            "secondary_trait": "verbose",
            "emotional_tone": "enthusiastic",
            "assertiveness": 0.7,
            "avg_sentence_length": "long",
            "uses_emoji": False,
            "uses_bullet_points": True,
            "vocabulary_level": "standard",
        },
        "greeting_templates": [
            "Dear James,",
            "Hi James,",
            "Good afternoon James,",
        ],
        "signoff_templates": [
            "Best regards,\nJames Carter\nSenior Recruiter, TalentScout",
            "Kind regards,\nJames Carter",
            "Warm regards,\nJames Carter",
        ],
        "power_dynamic": "vendor",
        "familiarity": 0.15,
        "trust_level": 0.3,
    },
    "dr_sarah_mitchell": {
        "name": "Dr. Sarah Mitchell",
        "emails": ["appointments@smiledental.co.uk"],
        "role": "Dentist",
        "company": "Smile Dental",
        "relationship_to_owner": "vendor",
        "traits": {
            "formality": "formal",
            "primary_trait": "direct",
            "secondary_trait": "diplomatic",
            "emotional_tone": "neutral",
            "assertiveness": 0.65,
            "avg_sentence_length": "medium",
            "uses_emoji": False,
            "uses_bullet_points": True,
            "vocabulary_level": "standard",
        },
        "greeting_templates": ["Dear James,", "Dear Mr. Hinton,", "Hello James,"],
        "signoff_templates": [
            "Best wishes,\nDr. Sarah Mitchell\nSmile Dental",
            "Kind regards,\nDr. Mitchell",
            "Regards,\nSmile Dental Team",
        ],
        "power_dynamic": "vendor",
        "familiarity": 0.2,
        "trust_level": 0.5,
    },
}

# ---------------------------------------------------------------------------
# Conversation thread topics per relationship type
# ---------------------------------------------------------------------------

THREAD_TOPICS = {
    "manager": [
        ("Sprint Planning Discussion", "sprint planning", "project timelines"),
        ("Q1 Performance Review", "performance feedback", "career goals"),
        ("Architecture Decision: Microservices", "technical architecture", "team direction"),
        ("Hiring for Senior Role", "recruitment", "team growth"),
        ("Production Incident Follow-up", "incident response", "process improvement"),
    ],
    "friend": [
        ("Weekend Plans", "social plans", "catching up"),
        ("That New Restaurant", "food recommendations", "social outing"),
        ("Football Sunday?", "sports", "social plans"),
        ("Job Update", "career chat", "advice"),
        ("Holiday Ideas", "travel planning", "vacation"),
    ],
    "client": [
        ("Project Status Update", "project progress", "deliverables"),
        ("Contract Renewal Discussion", "business terms", "pricing"),
        ("Feature Request: Dashboard Analytics", "requirements", "product feedback"),
        ("Invoice Query", "billing", "accounts"),
        ("Integration Timeline", "technical planning", "milestones"),
    ],
    "colleague": [
        ("Code Review: Auth Refactor", "code review", "technical feedback"),
        ("Bug in Payment Module", "debugging", "production issue"),
        ("Standup Notes", "daily update", "task progress"),
        ("Tech Stack Discussion", "architecture", "tooling"),
        ("Documentation Sprint", "documentation", "knowledge sharing"),
    ],
    "family": [
        ("Sunday Dinner?", "family gathering", "plans"),
        ("Mum's Birthday Present", "gift ideas", "family event"),
        ("Holiday Plans", "travel", "family trip"),
        ("How's Work Going?", "life update", "catching up"),
        ("Did You See This?", "sharing", "random chat"),
    ],
    "vendor": [
        ("Proposal for Redesign", "design work", "project scope"),
        ("Invoice Attached", "billing", "payment"),
        ("Design Review Feedback", "creative feedback", "revisions"),
        ("Availability Next Week", "scheduling", "project planning"),
        ("Portfolio Update", "capabilities", "new services"),
    ],
    "acquaintance": [
        ("Exciting Opportunity", "job opportunity", "recruitment"),
        ("Following Up", "networking", "connection"),
        ("Event Invitation", "conference", "networking"),
        ("Introduction", "professional intro", "first contact"),
        ("Quick Question About Your Stack", "technical question", "advice"),
    ],
}

# ---------------------------------------------------------------------------
# Message body templates — keyed by (formality, trait)
# ---------------------------------------------------------------------------

# Templates use {topic}, {detail}, {name}, {company} placeholders.
# Each returns a list of template strings.


def _templates_very_formal_analytical() -> list[str]:
    return [
        (
            "I wanted to bring to your attention the matter of {topic}. "
            "Having reviewed the relevant documentation and considered the "
            "implications, I believe we should proceed with careful deliberation. "
            "Specifically, I would recommend we address the following points:\n\n"
            "1. The current status of {detail} and its impact on our timeline\n"
            "2. The resource allocation required for successful completion\n"
            "3. Potential risks and mitigation strategies\n\n"
            "I would appreciate your considered response at your earliest convenience."
        ),
        (
            "Further to our previous correspondence regarding {topic}, I have "
            "conducted a thorough analysis of the situation. The data suggests "
            "that {detail} warrants immediate attention. I have prepared a "
            "comprehensive summary which I have attached for your review.\n\n"
            "Please do not hesitate to contact me should you require any "
            "clarification on the points raised herein."
        ),
        (
            "I am writing to formally document our discussion regarding {topic}. "
            "As per the agreed-upon framework, the following action items have "
            "been identified in relation to {detail}:\n\n"
            "- Assessment of current parameters\n"
            "- Evaluation of proposed modifications\n"
            "- Implementation timeline and resource requirements\n\n"
            "I trust this aligns with your expectations. Please advise if any "
            "amendments are necessary."
        ),
    ]


def _templates_very_formal_diplomatic() -> list[str]:
    return [
        (
            "I hope this message finds you well. I wanted to take a moment to "
            "discuss {topic} with you. I understand this is a matter that "
            "requires careful consideration, and I value your perspective. "
            "With regard to {detail}, I believe there may be an opportunity "
            "for us to find a mutually beneficial approach.\n\n"
            "I would welcome the chance to discuss this further at your convenience."
        ),
        (
            "Thank you for your continued attention to {topic}. I appreciate "
            "the effort you have invested thus far. Regarding {detail}, I "
            "would like to suggest that we explore some additional options "
            "that may prove advantageous for all parties involved.\n\n"
            "Please let me know when would be a suitable time to discuss."
        ),
    ]


def _templates_formal_diplomatic() -> list[str]:
    return [
        (
            "I wanted to follow up on {topic}. I've been thinking about "
            "{detail} and I believe we have a good opportunity here. "
            "I'd like to suggest we take a measured approach and consider "
            "all angles before making a decision.\n\n"
            "Would you have some time this week to discuss? I'm happy to "
            "work around your schedule."
        ),
        (
            "Thanks for your input on {topic}. I've taken your feedback "
            "on board and I think we can find a great middle ground on "
            "{detail}. I've outlined a few options below that I think "
            "could work well for everyone involved.\n\n"
            "Let me know your thoughts when you get a chance."
        ),
        (
            "I hope you're having a good week. I wanted to touch base on "
            "{topic} — specifically around {detail}. I think there's a "
            "way we can approach this that addresses everyone's concerns "
            "while keeping us on track.\n\n"
            "Happy to discuss further whenever suits you."
        ),
    ]


def _templates_formal_direct() -> list[str]:
    return [
        (
            "I need to discuss {topic} with you. Regarding {detail}, "
            "here's where we stand:\n\n"
            "- Current status: on track\n"
            "- Key blocker: resource availability\n"
            "- Next step: your decision required\n\n"
            "Please review and let me know your decision by end of week."
        ),
        (
            "Following up on {topic}. The {detail} situation needs your "
            "attention. I've reviewed the options and here's my recommendation:\n\n"
            "We should proceed with the original plan. The risk is manageable "
            "and the timeline is achievable.\n\n"
            "Let me know if you agree or want to discuss alternatives."
        ),
    ]


def _templates_semi_formal_direct() -> list[str]:
    return [
        (
            "Quick update on {topic}. The {detail} piece is sorted — "
            "I've pushed the changes and they're in review.\n\n"
            "Main points:\n"
            "- Tests passing\n"
            "- Performance looks good\n"
            "- Ready for your review\n\n"
            "Let me know if you spot anything."
        ),
        (
            "Heads up on {topic}. I've looked into {detail} and here's "
            "what I think we should do:\n\n"
            "Go with option A. It's cleaner, faster, and doesn't require "
            "any migration work. I can have it done by Thursday.\n\n"
            "Any objections?"
        ),
        (
            "Re: {topic}. I've dug into {detail} — the root cause was "
            "a race condition in the queue processor. Fix is straightforward. "
            "I'll have a PR up by end of day.\n\n"
            "Want me to also add monitoring for this edge case?"
        ),
    ]


def _templates_semi_formal_analytical() -> list[str]:
    return [
        (
            "I've been looking into {topic} and found some interesting patterns "
            "around {detail}. Here's a breakdown:\n\n"
            "- Latency has increased 15% over the past week\n"
            "- The spike correlates with the new caching layer\n"
            "- Rollback would resolve it but we'd lose the throughput gains\n\n"
            "I think the right approach is to profile the cache hit rate "
            "before making a call. Thoughts?"
        ),
        (
            "Ran the numbers on {topic}. For {detail}, the data is clear:\n\n"
            "- Option A: 40% cost reduction, 2-week implementation\n"
            "- Option B: 25% cost reduction, 3-day implementation\n"
            "- Option C: 60% cost reduction, 6-week implementation\n\n"
            "My recommendation is Option B given our current constraints. "
            "We can revisit Option C next quarter."
        ),
    ]


def _templates_semi_formal_terse() -> list[str]:
    return [
        "Re: {topic}. {detail} is done. PR is up. LGTM needed.",
        "Looked into {topic}. {detail} — it's a config issue. Fixed. Deploying now.",
        "{topic}: {detail} sorted. Tests green. Ship it?",
        "FYI: {topic} update. {detail} deployed to staging. No issues so far.",
    ]


def _templates_casual_direct() -> list[str]:
    return [
        (
            "Hey — just a quick one about {topic}. I think we should "
            "just go for it on {detail}. No point overthinking this one.\n\n"
            "Let me know and I'll get it done."
        ),
        (
            "So about {topic} — I've got a strong opinion on {detail}. "
            "We should ship the MVP and iterate. Waiting for perfect will "
            "cost us more than shipping something good enough.\n\n"
            "Agree? Disagree? Let's decide and move."
        ),
    ]


def _templates_casual_emotional() -> list[str]:
    return [
        (
            "I'm SO excited about {topic}! 🎉 The {detail} thing is "
            "coming together really nicely and I think it's going to "
            "look amazing when it's done.\n\n"
            "Can't wait to show you what I've been working on!"
        ),
        (
            "Just wanted to say — {topic} is going really well! "
            "I'm really happy with how {detail} turned out. "
            "It was a bit tricky at first but I think we nailed it 😊\n\n"
            "Let me know what you think!"
        ),
    ]


def _templates_casual_verbose() -> list[str]:
    return [
        (
            "So I've been thinking a lot about {topic} lately, and I wanted "
            "to share some thoughts with you. Basically, the way I see it, "
            "{detail} is really the key issue here. I've been going back and "
            "forth on this for a while, talking to a few people about it, and "
            "I think I've finally landed on what I think is the right approach. "
            "It's not perfect, but I think it gets us most of the way there. "
            "What do you reckon?"
        ),
        (
            "Hey! Hope you're doing well. I wanted to chat about {topic} — "
            "specifically about {detail}. I know we briefly touched on this "
            "last time we spoke, but I've had some more time to think about "
            "it and I actually think there might be a better way to handle it "
            "than what we originally discussed. I don't want to go into too "
            "much detail over email, but basically I think we should rethink "
            "the whole approach. Want to grab a quick call?"
        ),
    ]


def _templates_casual_diplomatic() -> list[str]:
    return [
        (
            "Hey, hope you're doing well! Just wanted to chat about {topic}. "
            "I know {detail} can be a bit tricky, but I think if we approach "
            "it the right way, we'll be fine. No rush on this — just wanted "
            "to put it on your radar.\n\n"
            "Let me know your thoughts whenever you get a chance 😊"
        ),
        (
            "Hi! Quick thought on {topic} — I was thinking about {detail} and "
            "wondered if you'd be open to trying a slightly different approach? "
            "Totally understand if you'd rather stick with the current plan, "
            "just thought it was worth mentioning!"
        ),
    ]


def _templates_very_casual_emotional() -> list[str]:
    return [
        ("omg {topic}!! 😂 can you believe {detail}?? " "this is absolutely mental haha"),
        (
            "mate {topic} is actually happening!! {detail} and I'm "
            "buzzing about it 🎉🎉 we need to celebrate"
        ),
        ("so {topic} right... {detail} is just 😍 " "honestly couldn't be happier about it"),
        (
            "hey!! hope you're good love 💕 just thinking about "
            "{topic}. {detail} sounds lovely don't you think? xxx"
        ),
    ]


def _templates_very_casual_terse() -> list[str]:
    return [
        "{topic}. {detail}. Thoughts?",
        "re {topic} — {detail}. done.",
        "yo {topic}. {detail}?",
        "{detail}. lmk.",
    ]


def _templates_very_casual_direct() -> list[str]:
    return [
        ("right {topic} — here's the deal with {detail}. " "just do it and stop overthinking 😄"),
        "{topic}: {detail}. sorted. next?",
        (
            "about {topic} — {detail} is fine, honestly don't worry "
            "about it. just get it done yeah?"
        ),
    ]


# Map (formality, primary_trait) -> template function
TEMPLATE_MAP: dict[tuple[str, str], callable] = {
    ("very_formal", "analytical"): _templates_very_formal_analytical,
    ("very_formal", "diplomatic"): _templates_very_formal_diplomatic,
    ("formal", "diplomatic"): _templates_formal_diplomatic,
    ("formal", "direct"): _templates_formal_direct,
    ("semi_formal", "direct"): _templates_semi_formal_direct,
    ("semi_formal", "analytical"): _templates_semi_formal_analytical,
    ("semi_formal", "terse"): _templates_semi_formal_terse,
    ("semi_formal", "diplomatic"): _templates_formal_diplomatic,  # reuse
    ("semi_formal", "emotional"): _templates_casual_emotional,  # reuse
    ("casual", "direct"): _templates_casual_direct,
    ("casual", "emotional"): _templates_casual_emotional,
    ("casual", "verbose"): _templates_casual_verbose,
    ("casual", "diplomatic"): _templates_casual_diplomatic,
    ("casual", "terse"): _templates_semi_formal_terse,  # reuse
    ("casual", "analytical"): _templates_semi_formal_analytical,  # reuse
    ("very_casual", "emotional"): _templates_very_casual_emotional,
    ("very_casual", "terse"): _templates_very_casual_terse,
    ("very_casual", "direct"): _templates_very_casual_direct,
}

# Fallback: if no exact match, use the closest formality
FORMALITY_FALLBACK: dict[str, callable] = {
    "very_formal": _templates_very_formal_analytical,
    "formal": _templates_formal_diplomatic,
    "semi_formal": _templates_semi_formal_direct,
    "casual": _templates_casual_direct,
    "very_casual": _templates_very_casual_emotional,
}


# ---------------------------------------------------------------------------
# Owner reply templates
# ---------------------------------------------------------------------------

OWNER_REPLY_TEMPLATES = [
    (
        "Thanks for the update on {topic}. I've had a look at {detail} "
        "and I think we're on the right track. A few thoughts:\n\n"
        "- Let's keep the scope tight for now\n"
        "- I'll review the PR this afternoon\n"
        "- We can revisit the broader approach next week\n\n"
        "Sound good?"
    ),
    (
        "Good shout on {topic}. Re {detail} — I agree with your approach. "
        "I'll get the implementation started today and push something up "
        "for review by end of day."
    ),
    (
        "Makes sense. For {topic}, I'd suggest we:\n\n"
        "1. Start with the minimal viable approach\n"
        "2. Get feedback early\n"
        "3. Iterate from there\n\n"
        "I'll take the lead on {detail} unless you'd rather handle it?"
    ),
    (
        "Noted on {topic}. The {detail} piece is important — I'll prioritise "
        "it this week. Let me know if anything changes in the meantime."
    ),
    (
        "Appreciate the heads up. I've been thinking about {topic} too. "
        "My take on {detail} is that we should move quickly but carefully. "
        "Happy to sync up on this if you want to chat through the approach."
    ),
    (
        "Got it. I'll look into {detail} today and come back with a plan. "
        "For {topic} more broadly, I think we're in good shape."
    ),
    (
        "Sounds like a plan. The {detail} side should be straightforward — "
        "I've dealt with something similar before. I'll have an update for "
        "you by tomorrow."
    ),
    (
        "Interesting point about {topic}. I hadn't considered {detail} from "
        "that angle. Let me dig into the data and get back to you with some "
        "numbers."
    ),
]

# Casual owner replies for family/friend contexts
OWNER_CASUAL_REPLY_TEMPLATES = [
    "Sounds good! Yeah {topic} sounds great. I'm in 👍",
    "Ha, yeah {detail} is brilliant. Let's do it!",
    ("Love it. Re {topic} — I'm free this weekend if that works? " "Let me know what time suits."),
    "Yeah that works for me. {topic} it is then!",
    "Nice one. I'll sort {detail} out and let you know.",
    "Haha yeah {topic} is mental. Definitely up for it though!",
]


# ---------------------------------------------------------------------------
# Message generation
# ---------------------------------------------------------------------------


def _get_templates_for_persona(persona: dict) -> list[str]:
    """Get body templates matching a persona's formality and primary trait."""
    traits = persona["traits"]
    formality = traits["formality"]
    primary_trait = traits["primary_trait"]

    key = (formality, primary_trait)
    if key in TEMPLATE_MAP:
        templates = TEMPLATE_MAP[key]()
    elif formality in FORMALITY_FALLBACK:
        templates = FORMALITY_FALLBACK[formality]()
    else:
        templates = _templates_semi_formal_direct()

    return templates


def _pick_topic(persona: dict, thread_idx: int) -> tuple[str, str, str]:
    """Pick a conversation topic for a persona's thread."""
    relationship = persona["relationship_to_owner"]
    topics = THREAD_TOPICS.get(relationship, THREAD_TOPICS["colleague"])
    topic = topics[thread_idx % len(topics)]
    return topic  # (subject, topic_word, detail_word)


def _build_message_body(
    persona: dict,
    topic_word: str,
    detail_word: str,
    is_owner: bool,
    relationship: str,
) -> str:
    """Build a message body from templates."""
    if is_owner:
        # Owner replies
        if relationship in ("friend", "family"):
            template = random.choice(OWNER_CASUAL_REPLY_TEMPLATES)
        else:
            template = random.choice(OWNER_REPLY_TEMPLATES)
        return template.format(topic=topic_word, detail=detail_word, name=persona["name"])
    else:
        # Contact messages
        templates = _get_templates_for_persona(persona)
        template = random.choice(templates)
        if isinstance(template, str):
            return template.format(
                topic=topic_word,
                detail=detail_word,
                name=OWNER_PERSONA["name"],
                company=persona.get("company", ""),
            )
        return str(template)


def _build_full_message(
    persona: dict,
    topic_word: str,
    detail_word: str,
    is_owner: bool,
    relationship: str,
) -> str:
    """Build a complete message with greeting + body + signoff."""
    body = _build_message_body(persona, topic_word, detail_word, is_owner, relationship)

    if is_owner:
        # Owner greeting + signoff
        recipient_name = persona["name"].split()[0]
        greeting = random.choice(OWNER_PERSONA["greeting_templates"]).format(name=recipient_name)
        signoff = random.choice(OWNER_PERSONA["signoff_templates"])
    else:
        # Contact greeting + signoff
        greeting = random.choice(persona["greeting_templates"])
        signoff = random.choice(persona["signoff_templates"])

    parts = []
    if greeting:
        parts.append(greeting)
    parts.append(body)
    if signoff:
        parts.append(signoff)

    return "\n\n".join(parts)


def _random_date(base: datetime | None = None) -> str:
    """Generate a random datetime string."""
    if base is None:
        base = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)
    offset = timedelta(
        days=random.randint(-30, 30),
        hours=random.randint(0, 12),
        minutes=random.randint(0, 59),
    )
    return (base + offset).isoformat()


def _owner_vocabulary_for_context(relationship: str) -> str:
    """Determine the owner's vocabulary level based on who they're writing to.

    The owner uses casual/simple language with family and friends, and
    standard professional language with colleagues and clients.  Only
    genuinely technical discussion warrants 'technical'.
    """
    if relationship in ("friend", "family"):
        return "simple"
    return "standard"


def _build_expected_personality(
    persona: dict,
    is_owner: bool,
    greeting: str,
    signoff: str,
    relationship: str = "",
) -> dict:
    """Build the ground truth personality signal for a message."""
    if is_owner:
        source = OWNER_PERSONA
        traits = source["traits"]
        return {
            "author_role": "owner",
            "writing_style": {
                "formality": traits["formality"],
                "avg_sentence_length": traits["avg_sentence_length"],
                "uses_greeting": bool(greeting),
                "greeting_style": greeting,
                "uses_signoff": bool(signoff),
                "signoff_style": signoff,
                "uses_emoji": traits["uses_emoji"],
                "uses_bullet_points": traits["uses_bullet_points"],
                "vocabulary_level": _owner_vocabulary_for_context(relationship),
            },
            "communication": {
                "primary_trait": traits["primary_trait"],
                "secondary_trait": traits["secondary_trait"],
                "emotional_tone": traits["emotional_tone"],
                "assertiveness": traits["assertiveness"],
            },
            "relationship": {
                "familiarity": persona.get("familiarity", 0.5),
                "power_dynamic": _invert_power_dynamic(persona.get("power_dynamic", "peer")),
                "trust_level": persona.get("trust_level", 0.5),
            },
        }
    else:
        traits = persona["traits"]
        return {
            "author_role": "contact",
            "writing_style": {
                "formality": traits["formality"],
                "avg_sentence_length": traits["avg_sentence_length"],
                "uses_greeting": bool(greeting),
                "greeting_style": greeting,
                "uses_signoff": bool(signoff),
                "signoff_style": signoff,
                "uses_emoji": traits["uses_emoji"],
                "uses_bullet_points": traits["uses_bullet_points"],
                "vocabulary_level": traits["vocabulary_level"],
            },
            "communication": {
                "primary_trait": traits["primary_trait"],
                "secondary_trait": traits.get("secondary_trait"),
                "emotional_tone": traits["emotional_tone"],
                "assertiveness": traits["assertiveness"],
            },
            "relationship": {
                "familiarity": persona.get("familiarity", 0.5),
                "power_dynamic": persona.get("power_dynamic", "peer"),
                "trust_level": persona.get("trust_level", 0.5),
            },
        }


def _invert_power_dynamic(dynamic: str) -> str:
    """Invert a power dynamic for the owner's perspective.

    If the contact is 'superior' to the owner, the owner is 'subordinate'
    relative to the contact.
    """
    inversions = {
        "superior": "subordinate",
        "subordinate": "superior",
        "client": "vendor",
        "vendor": "client",
        "peer": "peer",
    }
    return inversions.get(dynamic, dynamic)


def generate_dataset(
    threads_per_persona: int = 2,
    messages_per_thread: int = 10,
    seed: int = 42,
) -> dict:
    """Generate the complete personality conversation dataset.

    Returns:
        Dataset dict with metadata, personas, and messages.
    """
    random.seed(seed)

    all_messages = []
    msg_counter = 0
    base_date = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)

    for persona_key, persona in CONTACT_PERSONAS.items():
        for thread_idx in range(threads_per_persona):
            subject, topic_word, detail_word = _pick_topic(persona, thread_idx)
            thread_id = f"conv_{persona_key}_{thread_idx + 1:02d}"

            # Track thread date progression
            thread_base = base_date + timedelta(
                days=random.randint(-30, 30),
                hours=random.randint(7, 18),
            )

            for msg_idx in range(messages_per_thread):
                msg_counter += 1
                is_owner = msg_idx % 2 == 1  # Contact starts, then alternates

                # Pick email for contact (occasionally use alt email for alias testing)
                if is_owner:
                    from_email = OWNER_PERSONA["email"]
                    to_email = random.choice(persona["emails"])
                else:
                    from_email = random.choice(persona["emails"])
                    to_email = OWNER_PERSONA["email"]

                # Build greeting and signoff separately for ground truth tracking
                if is_owner:
                    recipient_name = persona["name"].split()[0]
                    greeting = random.choice(OWNER_PERSONA["greeting_templates"]).format(
                        name=recipient_name
                    )
                    signoff = random.choice(OWNER_PERSONA["signoff_templates"])
                else:
                    greeting = random.choice(persona["greeting_templates"])
                    signoff = random.choice(persona["signoff_templates"])

                # Build message body
                body = _build_message_body(
                    persona, topic_word, detail_word, is_owner, persona["relationship_to_owner"]
                )

                # Assemble full message
                parts = []
                if greeting:
                    parts.append(greeting)
                parts.append(body)
                if signoff:
                    parts.append(signoff)
                full_body = "\n\n".join(parts)

                # Thread date progression (each reply is 1-8 hours later)
                msg_date = thread_base + timedelta(
                    hours=msg_idx * random.randint(1, 8),
                    minutes=random.randint(0, 59),
                )

                # Build ground truth
                expected = _build_expected_personality(
                    persona, is_owner, greeting, signoff, persona["relationship_to_owner"]
                )

                message = {
                    "message_id": f"{thread_id}_msg_{msg_counter:04d}",
                    "thread_id": thread_id,
                    "thread_position": msg_idx + 1,
                    "persona_key": persona_key,
                    "subject": f"Re: {subject}" if msg_idx > 0 else subject,
                    "from_email": from_email,
                    "to_emails": [to_email],
                    "body_text": full_body,
                    "received_at": msg_date.isoformat(),
                    "author_is_owner": is_owner,
                    "expected_personality": expected,
                }

                all_messages.append(message)

    # Sort by thread_id then thread_position for clean ordering
    all_messages.sort(key=lambda m: (m["thread_id"], m["thread_position"]))

    return {
        "metadata": {
            "source": "generated",
            "created_at": datetime.now(tz=UTC).isoformat(),
            "description": (
                f"{len(all_messages)} messages across "
                f"{len(CONTACT_PERSONAS)} personas, "
                f"{threads_per_persona} threads each, "
                f"{messages_per_thread} messages per thread"
            ),
            "count": len(all_messages),
            "seed": seed,
            "personas": len(CONTACT_PERSONAS),
            "threads_per_persona": threads_per_persona,
            "messages_per_thread": messages_per_thread,
            "owner": {
                "name": OWNER_PERSONA["name"],
                "email": OWNER_PERSONA["email"],
            },
        },
        "owner_persona": {
            "name": OWNER_PERSONA["name"],
            "email": OWNER_PERSONA["email"],
            "traits": OWNER_PERSONA["traits"],
            "preferences": OWNER_PERSONA["preferences"],
        },
        "contact_personas": {
            key: {
                "name": p["name"],
                "emails": p["emails"],
                "role": p["role"],
                "company": p["company"],
                "relationship_to_owner": p["relationship_to_owner"],
                "traits": p["traits"],
                "power_dynamic": p.get("power_dynamic", "peer"),
                "familiarity": p.get("familiarity", 0.5),
                "trust_level": p.get("trust_level", 0.5),
            }
            for key, p in CONTACT_PERSONAS.items()
        },
        "messages": all_messages,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate synthetic personality conversation dataset"
    )
    parser.add_argument("--threads-per-persona", type=int, default=2)
    parser.add_argument("--messages-per-thread", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--output",
        default="benchmarks/datasets/personality_conversations.json",
    )
    args = parser.parse_args()

    dataset = generate_dataset(
        threads_per_persona=args.threads_per_persona,
        messages_per_thread=args.messages_per_thread,
        seed=args.seed,
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(dataset, indent=2))

    print(f"Generated {dataset['metadata']['count']} messages to {output_path}")
    print(f"  Personas: {dataset['metadata']['personas']}")
    print(f"  Threads per persona: {dataset['metadata']['threads_per_persona']}")
    print(f"  Messages per thread: {dataset['metadata']['messages_per_thread']}")

    # Show persona summary
    print("\nPersona summary:")
    for key, p in CONTACT_PERSONAS.items():
        traits = p["traits"]
        print(
            f"  {key:25s} | {traits['formality']:12s} | {traits['primary_trait']:12s} "
            f"| {traits['emotional_tone']:12s} | {p['relationship_to_owner']}"
        )


if __name__ == "__main__":
    main()
