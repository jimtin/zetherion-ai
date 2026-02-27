#!/usr/bin/env python3
"""Generate a large synthetic email dataset for classification benchmarking.

Produces realistic, diverse emails across all 15 EmailCategory types with
extensive edge-case coverage: thread depth, urgency calibration, mixed
languages, minimal context, code snippets, spam-like legitimate emails,
ambiguous categories, forwarded chains, and more.

Usage:
    python scripts/generate-email-dataset.py --count 1000 --output benchmarks/datasets/synthetic_1000.json
"""

import argparse
import json
import random
import string
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Sender pools
# ---------------------------------------------------------------------------

KNOWN_CONTACTS = [
    ("Sarah Chen", "sarah.chen@acmecorp.com", "Project Manager", "Acme Corp"),
    ("Mike Johnson", "mike.johnson@acmecorp.com", "Backend Engineer", "Acme Corp"),
    ("Lisa Patel", "lisa.patel@acmecorp.com", "Frontend Engineer", "Acme Corp"),
    ("David Martinez", "david.martinez@clientco.com", "Head of Technology", "ClientCo"),
    ("Emily Brooks", "legal@clientco.com", "Legal Counsel", "ClientCo"),
    ("Tom Wilson", "tom.wilson@gmail.com", "Friend", ""),
    ("Jane Hinton", "sister.jane@gmail.com", "Sister", ""),
    ("Mark Hinton", "brother.mark@gmail.com", "Brother", ""),
    ("Rachel", "vendor.design@creativestudio.io", "Designer", "Creative Studio"),
    ("Alex Rivera", "ceo@startupxyz.com", "CEO", "StartupXYZ"),
    ("Robert Chen", "ceo@acmecorp.com", "CEO", "Acme Corp"),
    ("Dr. Sarah Mitchell", "appointments@smiledental.co.uk", "Dentist", "Smile Dental"),
    ("Ahmed Khan", "ahmed.khan@techpartners.io", "CTO", "Tech Partners"),
    ("Sophie Williams", "sophie@freelancedev.co.uk", "Freelancer", ""),
    ("Carlos Rodriguez", "carlos@investmentfirm.com", "Account Manager", "InvestCo"),
    ("Priya Sharma", "priya.sharma@acmecorp.com", "QA Lead", "Acme Corp"),
    ("James Carter", "james.carter@openai.com", "Researcher", "OpenAI"),
    ("Mum", "mum@gmail.com", "Mum", ""),
    ("Dad", "dad@outlook.com", "Dad", ""),
    ("Nadia Petrova", "nadia@designagency.com", "Creative Director", "Design Agency"),
]

NOREPLY_SENDERS = [
    "noreply@github.com", "noreply@gitlab.com", "no-reply@accounts.google.com",
    "noreply@linkedin.com", "noreply@vercel.com", "noreply@aws.amazon.com",
    "noreply@cloudflare.com", "noreply@stripe.com", "noreply@sentry.io",
    "noreply@docker.com", "noreply@notion.so", "noreply@slack.com",
    "noreply@digitalocean.com", "no-reply@monzo.com", "noreply@spotify.com",
    "noreply@meetup.com", "do-not-reply@stackoverflow.email",
    "calendar-noreply@google.com", "shipment-tracking@amazon.co.uk",
    "no-reply@apple.com", "noreply@uber.com", "noreply@deliveroo.co.uk",
    "MAILER-DAEMON@mail.zetherion.com", "noreply@twilio.com",
    "alerts@pagerduty.com", "noreply@datadog.com",
]

MARKETING_SENDERS = [
    "deals@fashionstore.com", "newsletter@techdaily.io", "promo@saasplatform.com",
    "offers@travelsite.com", "hello@productlaunch.io", "team@newsletter.dev",
    "updates@startup-weekly.com", "digest@hackernewsletter.com",
    "info@conference2026.com", "events@richmondrunners.org",
    "hello@indiemakers.co", "founders@ycombinator.com",
    "weekly@pythonweekly.com", "news@tldr.tech", "content@medium.com",
]

RECRUITER_SENDERS = [
    ("Emma Thompson", "emma.recruiter@revolut.com", "Senior Tech Recruiter", "Revolut"),
    ("Jack Chen", "jack.c@google.com", "Technical Recruiter", "Google"),
    ("Sofia Martinez", "sofia@meta.com", "Engineering Recruiter", "Meta"),
    ("Ryan O'Brien", "ryan@startup-talent.io", "Founder", "Startup Talent"),
    ("Aisha Patel", "aisha@amazon.jobs", "Senior Recruiter", "Amazon"),
    ("Chris Wu", "chris.wu@stripe.com", "People Team", "Stripe"),
]

RECIPIENT = "james@zetherion.com"

# ---------------------------------------------------------------------------
# Template pools organised by category
# ---------------------------------------------------------------------------

# Each template is (subject, body, edge_case_tags)
# Edge case tags help ensure coverage


def _personal_templates():
    return [
        (
            "Hey! Dinner plans for Saturday?",
            "Hey mate,\n\nAre you still up for dinner this Saturday? I was thinking we could try that new Japanese place in Soho. Thinking around 7:30pm?\n\nCheers,\n{name}",
            ["casual", "question"],
        ),
        (
            "Mum's birthday planning",
            "Hi both,\n\nMum's 60th is coming up on March 8th. Here's what I'm thinking:\n- Venue: Italian restaurant in Richmond\n- Time: 7pm\n- Guest list: ~25 people\n- Present: Weekend spa break?\n\n{name} - can you sort out a slideshow of old family photos?\n\nJane x",
            ["family", "action_items", "multi_recipient"],
        ),
        (
            "Quick favour",
            "Hey, can you pick up some milk on the way home? Running low.\n\nTa",
            ["minimal_context", "terse", "personal_errand"],
        ),
        (
            "Photo from the weekend",
            "Haha, look at this photo from Saturday! You look absolutely ridiculous 😂\n\nAttached: IMG_4829.jpg",
            ["attachment", "casual", "emoji"],
        ),
        (
            "Thinking of you",
            "Hi love,\n\nJust wanted to say I'm thinking of you. Hope work isn't too stressful. Let's catch up over the weekend.\n\nLots of love,\nMum xx",
            ["emotional", "family", "no_action"],
        ),
        (
            "Wedding invitation - Save the date!",
            "Dear James,\n\nWe are delighted to invite you to our wedding!\n\nDate: Saturday, June 20, 2026\nVenue: Kew Gardens, Richmond\nTime: 2:00 PM\n\nRSVP by April 30th.\n\nWith love,\nTom & Emma",
            ["event", "formal", "deadline"],
        ),
        (
            "Can you lend me your drill?",
            "Mate, I need to put up some shelves this weekend. Any chance I can borrow your drill? I'll drop it back Sunday.\n\nTom",
            ["casual", "request", "minimal"],
        ),
        (
            "RE: Holiday plans",
            "Yeah that works for me! Let's book the Airbnb then. Can you send me the link again?\n\nI'll sort flights.",
            ["thread", "casual", "action_items"],
        ),
        (
            "",
            "👍",
            ["empty_subject", "emoji_only", "minimal"],
        ),
        (
            "Fwd: Funny article",
            "LOL you need to read this\n\n---------- Forwarded message ----------\nFrom: randomsite@news.com\nSubject: 10 Things Programmers Will Never Admit\n\n[Article content about programmer habits...]",
            ["forwarded", "casual", "no_action"],
        ),
        (
            "Are you ok?",
            "Hey, tried calling you earlier but went straight to voicemail. Everything alright? Give me a ring when you can.\n\n{name}",
            ["concern", "urgent_personal", "call_request"],
        ),
        (
            "Happy Birthday! 🎂",
            "Happy birthday mate! Hope you have a brilliant day. Drinks on me next time we're out.\n\nCheers,\n{name}",
            ["celebration", "emoji", "no_action"],
        ),
    ]


def _work_colleague_templates():
    return [
        (
            "Re: Q1 Sprint Planning - Updated Timeline",
            "Hi James,\n\nFollowing up on our discussion yesterday. The key milestones:\n1. Backend API refactor - Feb 20\n2. Frontend integration - Feb 27\n3. QA cycle - Mar 5\n4. Release candidate - Mar 10\n\nCan you review and confirm these dates?\n\nBest,\n{name}\n{title}, {company}",
            ["thread", "deadlines", "action_required"],
        ),
        (
            "Quick question about the API rate limiting",
            "Hey James,\n\nSeeing 429 responses from our gateway during load tests. Current limit is 100 req/s per client.\n\nShould I:\na) Increase the limit for load test client?\nb) Add the load test IP to whitelist?\nc) Use a different auth token?\n\nNeed to finish load test report by tomorrow.\n\nMike",
            ["question", "options", "deadline"],
        ),
        (
            "Re: Bug in user dashboard - charts not loading",
            "Hey James,\n\nTraced the chart loading issue. Race condition in useEffect:\n\n```javascript\nuseEffect(() => {\n  let mounted = true;\n  const init = async () => {\n    await chartLib.ready();\n    if (mounted) {\n      const data = await fetchDashboardData();\n      setChartData(data);\n    }\n  };\n  init();\n  return () => { mounted = false; };\n}, []);\n```\n\nPushing fix this afternoon. Can you review?\n\n{name}",
            ["code_snippet", "bug_fix", "review_request"],
        ),
        (
            "Meeting notes: Product roadmap review",
            "Hi team,\n\nDecisions made:\n1. Email classification engine moves to P0\n2. Dark mode pushed to Q2\n3. Approved: Groq API budget ($200/month)\n4. Spike: RAG improvements for knowledge base\n\nAction items:\n- James: Email classification benchmark by Feb 21\n- Lisa: Dashboard performance audit by Feb 18\n- Mike: Infrastructure cost analysis by Feb 20\n\nNext review: February 28 at 2:00 PM\n\nSarah",
            ["meeting_notes", "action_items", "multi_recipient", "deadlines"],
        ),
        (
            "Fw: Important - Board meeting agenda for review",
            "James, FYI - forwarding this from the CEO. They want our technical input on the AI strategy section.\n\n--- Forwarded message ---\nFrom: {ceo_email}\nSubject: Board meeting agenda\n\nSarah, need from your team:\n1. Technical progress report on AI platform\n2. Infrastructure cost projections for 10x scale\n3. Competitive analysis vs 3 main competitors\n4. Demo-ready prototype of classification engine\n\nReady by February 25th.\n\nRobert Chen\nCEO",
            ["forwarded", "exec_visibility", "deadlines", "high_stakes"],
        ),
        (
            "URGENT: Production server down",
            "CRITICAL ALERT\n\nProd-api-03 returning 503s since 08:45 UTC. Error rate 45%.\n\nAffected: auth, payments, orders\n~2,000 users impacted\n\nJoin incident channel ASAP.\n\nIncident: INC-2847",
            ["urgent", "incident", "action_required"],
        ),
        (
            "Heads up - I'll be OOO next week",
            "Hi team,\n\nJust a heads up that I'll be on annual leave from Monday 24th to Friday 28th February. {name_2} will cover for me on anything urgent.\n\nI've updated Jira with handover notes. Let me know if you need anything before I go.\n\n{name}",
            ["info_only", "ooo", "no_action"],
        ),
        (
            "Code review feedback on PR #312",
            "Hey James,\n\nLeft some comments on your PR. Main things:\n\n1. The retry logic in `classify_email()` could use a circuit breaker - if Groq is down for 5 mins, we'll pile up retries\n2. Missing type hints on the `_parse_response` helper\n3. The test coverage for edge cases is thin - what happens with empty body?\n4. Nice work on the prompt template separation, very clean\n\nOverall LGTM with those fixes.\n\n{name}",
            ["code_review", "action_items", "positive_feedback"],
        ),
        (
            "Re: Re: Re: Re: Database migration plan",
            "Agreed. Let's go with option B then.\n\n{name}",
            ["deep_thread", "terse", "approval"],
        ),
        (
            "Standup update - Feb 14",
            "Yesterday: Finished the email classification schema PR\nToday: Starting benchmark script, will test against Groq models\nBlockers: None\n\n{name}",
            ["standup", "brief", "info_only"],
        ),
        (
            "Pair programming session?",
            "Hey James, free this afternoon for a pairing session? I'm stuck on the WebSocket connection pooling and could use a second pair of eyes. 2pm works for me.\n\n{name}",
            ["request", "scheduling", "informal"],
        ),
        (
            "RE: Deployment checklist",
            "+1, looks good. Ship it! 🚀",
            ["minimal", "approval", "emoji"],
        ),
        (
            "Performance review - self assessment due",
            "Hi James,\n\nReminder that your Q4 self-assessment is due by February 21st. Please complete it in Lattice.\n\nFocus areas:\n- Key achievements\n- Areas for growth\n- Goals for next quarter\n\nLet me know if you have questions.\n\n{name}\nEngineering Manager",
            ["hr_process", "deadline", "action_required"],
        ),
    ]


def _work_client_templates():
    return [
        (
            "Re: Contract renewal discussion",
            "James,\n\nReviewed the proposal. Few points:\n1. 15% rate increase is above budget. Phased approach?\n2. Extend to 12 months for volume discount?\n3. SLA terms look good, need P1 incident response time clarification.\n\nCall this week? Free Thursday PM or Friday AM.\n\nDavid Martinez\nHead of Technology, ClientCo",
            ["negotiation", "scheduling", "action_items"],
        ),
        (
            "URGENT: Client escalation - data discrepancy in reports",
            "James,\n\nJust off a call with ClientCo - data discrepancy in analytics reports. Their numbers show 15% higher conversion rates than our dashboard.\n\nP1 issue - David Martinez escalating to their VP if no answer by EOD tomorrow.\n\nCan you:\n1. Check analytics aggregation pipeline for data loss\n2. Compare raw event counts vs reported for January\n3. Review conversion rate calculation logic\n\nJoined war room in Slack #clientco-data-issue. Please join ASAP.\n\nThis is our biggest client.\n\nSarah",
            ["urgent", "escalation", "client_risk", "action_items"],
        ),
        (
            "Re: Partnership proposal - AI integration",
            "Hi James,\n\nBeen thinking more about our discussion at the conference. Proposed partnership:\n1. Integrate Zetherion AI into our customer service platform\n2. Revenue share: 70/30 (us/you)\n3. Joint go-to-market for enterprise\n4. Co-branded case studies\n\n500+ SMBs, growing 25% MoM.\n\nMeeting next week for commercial terms?\n\nAlex Rivera\nCEO, StartupXYZ",
            ["partnership", "business_dev", "scheduling"],
        ),
        (
            "Thank you for your help yesterday",
            "Hi James,\n\nJust wanted to say thanks for jumping on that call yesterday to troubleshoot the API issues. Your team's responsiveness is exactly why we renewed our contract.\n\nLooking forward to continuing the partnership.\n\nBest,\nDavid",
            ["positive_feedback", "no_action", "relationship"],
        ),
        (
            "Re: Consulting engagement - SOW review",
            "Hi James,\n\nLegal has reviewed the SOW. Required changes:\n1. Section 3.2 - IP Assignment: explicit assignment of all deliverables\n2. Section 5.1 - Liability Cap: raise from £10k to £50k\n3. Section 7 - Termination: add 30-day notice period\n4. Section 8 - Confidentiality: extend NDA from 2 to 5 years\n\nUpdated SOW needed ASAP. Hoping to sign by month end.\n\nEmily Brooks\nLegal Counsel, ClientCo",
            ["legal", "contract", "deadline", "action_required"],
        ),
        (
            "Quarterly business review - March 5",
            "Hi James,\n\nScheduling our QBR for March 5th at 2pm. Agenda:\n- Platform usage metrics\n- Support ticket trends\n- Roadmap preview\n- Contract renewal discussion\n\nPlease prepare a deck covering the first two items. Template in shared drive.\n\nDavid",
            ["meeting", "preparation", "deadline"],
        ),
        (
            "Re: Can we reschedule Thursday's call?",
            "Hi James,\n\nSorry for the late notice, something came up. Would Friday 10am work instead? Also free Monday morning next week.\n\nApologies for the inconvenience.\n\nDavid",
            ["reschedule", "question", "polite"],
        ),
        (
            "New feature request - bulk export",
            "Hi James,\n\nOur operations team needs a bulk data export feature. Requirements:\n- Export last 90 days of analytics data\n- CSV and JSON formats\n- Filter by date range, user segment, event type\n- Scheduled exports (weekly)\n\nThis is blocking our compliance reporting. How quickly could you deliver this?\n\nDavid",
            ["feature_request", "requirements", "deadline_question"],
        ),
    ]


def _work_vendor_templates():
    return [
        (
            "Re: Re: Re: Freelance project handoff",
            "Hi James,\n\nFinal deliverables for the landing page redesign:\n1. Figma file (12 pages, mobile + desktop)\n2. Asset export ZIP (SVGs, PNGs @2x)\n3. Style guide with brand tokens\n4. Lottie files for 3 hero animations\n\nTotal: 42 hours @ £75/hr = £3,150\nInvoice follows separately.\n\nCheers,\nRachel\nCreative Studio",
            ["deliverables", "invoice_pending", "thread"],
        ),
        (
            "Invoice #INV-2026-0847 from Catalyst Solutions",
            "Dear James Hinton,\n\nInvoice for January 2026:\n\nInvoice Number: INV-2026-0847\nDate: February 1, 2026\nDue Date: February 28, 2026\n\nSoftware Development Services - January 2026\nAmount: £4,500.00\nVAT (20%): £900.00\nTotal: £5,400.00\n\nPayment Details:\nBarclays, Sort: 20-45-67, Acc: 43829105\nRef: INV-2026-0847\n\nAccounts Team\nCatalyst Solutions Ltd",
            ["invoice", "payment_due", "financial"],
        ),
        (
            "Your hosting plan is expiring",
            "Hi James,\n\nYour annual hosting plan expires on March 1, 2026.\n\nCurrent plan: Pro (50GB SSD, 4 vCPU)\nRenewal price: £299/year (was £249 - 20% increase)\n\nRenew before Feb 28 to keep your current price.\n\nAlternatively, migrate to our new cloud platform for £199/year with better specs.\n\nSupport Team\nWebHost Pro",
            ["renewal", "price_increase", "deadline", "decision"],
        ),
        (
            "SLA breach notification - February",
            "Dear Customer,\n\nWe are writing to inform you of an SLA breach that occurred on February 10, 2026.\n\nDetails:\n- Service: API Gateway\n- Guaranteed uptime: 99.9%\n- Actual uptime: 99.7%\n- Downtime: 43 minutes\n- Root cause: Database failover delay\n\nPer your contract, you are entitled to a 10% service credit.\n\nCredit amount: £45.00\nApplied to: March invoice\n\nWe apologize for the inconvenience.\n\nInfrastructure Team",
            ["sla_breach", "credit", "formal"],
        ),
    ]


def _transactional_templates():
    return [
        (
            "Your Amazon order has shipped!",
            "Hello James,\n\nOrder #{order_num} has shipped.\n\nItems:\n- {item}\n\nEstimated delivery: {delivery_date}\nCarrier: Royal Mail\nTracking: RM{tracking}\n\nThank you for shopping with us.",
            ["shipping", "tracking"],
        ),
        (
            "Your Deliveroo order is on its way!",
            "Your order is on its way! 🛵\n\nOrder #DLV-{order_num}\nFrom: {restaurant}\n\nEstimated arrival: 35-45 minutes\n\nTrack your order in the app.\n\nTotal: £{amount}",
            ["food_delivery", "emoji", "no_action"],
        ),
        (
            "Your Uber ride receipt",
            "Thanks for riding with Uber\n\nTrip on {date}\n\nPickup: {pickup}\nDropoff: {dropoff}\n\nTrip fare: £{fare}\nTotal: £{total}\n\nPayment: Visa •••• 4829",
            ["receipt", "no_action"],
        ),
        (
            "Thank you for your purchase - Receipt",
            "Apple Receipt\n\nApple ID: james@zetherion.com\nDate: {date}\n\nSubscription Renewal:\n- Apple Music - £10.99/month\n- iCloud+ (200GB) - £2.99/month\n\nTotal: £13.98\nPayment: Visa ending in 4829",
            ["subscription", "receipt", "auto"],
        ),
        (
            "Password reset request for your Notion account",
            "Hi James,\n\nWe received a request to reset the password for your Notion account.\n\nClick the link below to reset:\nhttps://www.notion.so/reset-password?token=abc123\n\nThis link expires in 24 hours.\n\nIf you didn't request this, ignore this email.\n\nNotion Team",
            ["security", "password_reset", "time_sensitive"],
        ),
        (
            "Your prescription is ready for collection",
            "Dear James,\n\nYour prescription is ready at:\nBoots Pharmacy, 15 High Street, Richmond\n\nCollection from: {date}\nCollect within 14 days.\nPrescription: RX-2026-{rx_num}\n\nBoots UK",
            ["health", "action_required", "deadline"],
        ),
        (
            "Reminder: Dentist appointment tomorrow",
            "Appointment Reminder\n\nDate: {date}\nTime: 10:30 AM\nDentist: Dr. Sarah Mitchell\nAddress: 42 High Street, Richmond, TW9 1AA\n\nArrive 5 minutes early.\n\nSmile Dental Practice",
            ["appointment", "reminder", "time_sensitive"],
        ),
        (
            "Gym membership renewal",
            "Hi James,\n\nYour PureGym membership renews on March 1, 2026.\n\nPlan: Plus (Peak access)\nFee: £29.99/month\n\nAuto-renews unless cancelled before Feb 28.\n\nPureGym",
            ["subscription", "renewal", "deadline"],
        ),
        (
            "Your parcel could not be delivered",
            "We tried to deliver your parcel today but no one was available.\n\nTracking: RM{tracking}\nNext attempt: Tomorrow between 8am-6pm\n\nAlternatively, collect from: Richmond Post Office (after 24 hours)\n\nRoyal Mail",
            ["delivery_failed", "action_required"],
        ),
        (
            "Undelivered Mail Returned to Sender",
            "This is the mail system at host mail.zetherion.com.\n\nYour message could not be delivered:\n\nRecipient: old.contact@defunct-company.com\nReason: 550 5.1.1 The email account does not exist\n\nOriginal subject: Following up on our conversation\n\nPostfix",
            ["bounce", "system", "no_action"],
        ),
        (
            "Your flight itinerary - Confirmation",
            "Booking Confirmation\n\nBooking Ref: BA7829\nPassenger: James Hinton\n\nOutbound: London Heathrow → Barcelona\nDate: March 15, 2026\nFlight: BA478, 06:30 - 09:45\n\nReturn: Barcelona → London Heathrow\nDate: March 19, 2026\nFlight: BA479, 20:15 - 21:30\n\nTotal: £245.00\n\nBritish Airways",
            ["travel", "booking", "important"],
        ),
        (
            "Your Trainline ticket",
            "e-Ticket\n\nLondon Waterloo → Richmond\nDate: {date}\nDepart: 17:42 | Arrive: 17:59\nClass: Standard\n\nTicket ref: TL-{ref}\nPrice: £4.80\n\nShow this email or use the app.\n\nTrainline",
            ["travel", "ticket", "no_action"],
        ),
    ]


def _newsletter_templates():
    return [
        (
            "Weekly Tech Digest: AI Breakthroughs & Developer Tools",
            "TECH DAILY WEEKLY DIGEST - {date}\n\nTop Stories:\n1. Claude 4.5 Benchmarks Show 40% Improvement\n2. Rust 2.0 Release Candidate Available\n3. GitHub Copilot Gets Multi-File Context\n4. PostgreSQL 17 Performance Deep Dive\n5. The State of WebAssembly in 2026\n\nRead more at techdaily.io\n\nUnsubscribe: https://techdaily.io/unsubscribe",
            ["digest", "tech", "unsubscribe"],
        ),
        (
            "Python Weekly - Issue #634",
            "PYTHON WEEKLY\n\nArticles & Tutorials:\n- Building Production RAG Systems with LangChain\n- Type Safety Beyond mypy\n- Asyncio Patterns for Real-World Applications\n- Django 5.1 Migration Guide\n\nProjects & Code:\n- fasthtml: HTML-first Python web framework\n- uv: Ultra-fast Python package installer\n\nPython Weekly\nUnsubscribe: pythonweekly.com/unsub",
            ["digest", "python", "curated"],
        ),
        (
            "TLDR Newsletter - Feb 14, 2026",
            "TLDR\n\n📱 Big Tech & Startups\n- Apple announces M4 Ultra chip with 192GB unified memory\n- Anthropic raises $5B Series E at $60B valuation\n\n🧠 Science & Tech\n- New breakthrough in room-temperature superconductors\n\n💻 Programming\n- Bun 2.0 released with native Windows support\n\nUnsubscribe",
            ["digest", "mixed", "emoji"],
        ),
        (
            "Hacker Newsletter - Top Links This Week",
            "This week on Hacker News:\n\n1. Show HN: I built a terminal-based email client in Rust (482 points)\n2. Why I quit my $400k job at Google (1.2k points)\n3. The Hidden Cost of AI Code Generation (367 points)\n4. Ask HN: What are you working on? February 2026\n\nhackernewsletter.com",
            ["digest", "hn", "curated"],
        ),
        (
            "Your Substack digest",
            "New posts from your subscriptions:\n\n📝 Simon Willison - \"LLM Tool Use Patterns\"\n📝 Julia Evans - \"Debugging DNS: a comic\"\n📝 Charity Majors - \"The Future of Observability\"\n\nRead in app or on web.\n\nSubstack",
            ["digest", "blog", "personal_feed"],
        ),
    ]


def _marketing_templates():
    return [
        (
            "50% OFF Everything - Valentine's Day Flash Sale!",
            "❤️ VALENTINE'S DAY FLASH SALE ❤️\n\n50% OFF EVERYTHING - TODAY ONLY!\n\nUse code LOVE50 at checkout.\nFree shipping over £50\nFree returns within 30 days\n\nOffer valid until midnight.\n\nUnsubscribe: fashionstore.com/unsubscribe",
            ["sale", "urgency_fake", "emoji", "unsubscribe"],
        ),
        (
            "Last chance: 70% off annual plan",
            "Hi James,\n\nYour trial expires tomorrow. Lock in 70% off our annual plan before it's too late.\n\nMonthly: £29/month\nAnnual (70% off): £8.70/month (billed £104.40/year)\n\nClaim your discount →\n\nSaaS Platform Team",
            ["trial_ending", "urgency_fake", "pricing"],
        ),
        (
            "You left something in your cart",
            "Hi James,\n\nYou left this in your cart:\n\n- Sony WH-1000XM5 Headphones (£279.00)\n\nComplete your purchase before it sells out!\n\nFree next-day delivery for Prime members.\n\nAmazon",
            ["abandoned_cart", "urgency_fake"],
        ),
        (
            "Introducing our new AI-powered features",
            "Hi James,\n\nWe're excited to announce 3 new AI features:\n\n1. Smart Compose - AI-assisted email writing\n2. Priority Inbox - ML-powered email sorting\n3. Meeting Summarizer - Auto-generated meeting notes\n\nTry them free for 14 days.\n\nThe ProductApp Team",
            ["product_launch", "feature_announcement"],
        ),
        (
            "You're invited: Exclusive webinar on AI in DevOps",
            "Join our free webinar!\n\nAI-Powered DevOps: From CI/CD to AIOps\nDate: February 25, 2026\nTime: 2:00 PM GMT\nSpeaker: Dr. Sarah Thompson, VP Engineering at DataPlatform\n\nRegister free: webinar.link/ai-devops\n\n500 spots remaining.\n\nDataPlatform Inc.",
            ["webinar", "event", "registration"],
        ),
        (
            "Charity run: Sign up for the Richmond 10K",
            "Richmond Runners Annual 10K\n\nDate: Sunday, March 15, 2026\nRoute: Richmond Park\nEntry: £25 (includes medal + t-shirt)\n\nAll proceeds to Richmond Youth Centre.\n\nRegister: richmondrunners.org/10k-2026\nEarly bird deadline: February 28\n\nRichmond Runners Club",
            ["charity", "event", "community"],
        ),
    ]


def _support_inbound_templates():
    return [
        (
            "Re: Re: API Integration Support - Webhook Issues",
            "Hi James,\n\nIdentified the issue. Webhook signature verification failing because middleware parses body before verification.\n\nFix:\n1. Capture raw body BEFORE JSON parsing\n2. Use `request.get_data()` for verification\n3. Pass raw bytes to `stripe.Webhook.construct_event()`\n\nCode sample attached.\n\nAlex Kim\nStripe Developer Support\nTicket #STR-482910",
            ["support_resolution", "code_guidance", "thread"],
        ),
        (
            "Ticket #4829: Your request has been received",
            "Hi James,\n\nWe've received your support request and assigned it ticket #4829.\n\nSubject: API returning 500 errors intermittently\nPriority: High\nEstimated response time: 4 hours\n\nYou can check status at: support.platform.com/tickets/4829\n\nSupport Team",
            ["ticket_ack", "auto", "tracking"],
        ),
        (
            "Re: Billing discrepancy on January invoice",
            "Hi James,\n\nI've investigated the billing discrepancy you reported.\n\nYou were charged for 2 additional API seats that were provisioned on Jan 12 when your team expanded. This matches the usage logs.\n\nHowever, I can see the seats were removed on Jan 25, so I've issued a prorated refund of £47.50.\n\nThe credit will appear on your next statement.\n\nBest,\nKaren\nBilling Support",
            ["billing", "resolution", "refund"],
        ),
    ]


def _support_outbound_templates():
    return [
        (
            "Your support ticket #3847 has been resolved",
            "Hi James,\n\nYour support ticket has been resolved.\n\nTicket: #3847\nSubject: Unable to connect OAuth\nResolution: Re-authenticated the OAuth connection and verified token refresh\n\nIf this doesn't resolve your issue, reply to reopen.\n\nSupport Team",
            ["resolution", "auto"],
        ),
        (
            "How was your support experience?",
            "Hi James,\n\nYour recent support ticket (#3847) was resolved. We'd love your feedback.\n\nRate your experience: ⭐⭐⭐⭐⭐\n\nOr reply with any comments.\n\nThanks!\nCustomer Success Team",
            ["feedback_request", "survey"],
        ),
    ]


def _financial_templates():
    return [
        (
            "Your Monzo statement is ready",
            "Hi James,\n\nJanuary 2026 statement ready.\n\nOpening: £3,245.67\nMoney in: £5,200.00\nMoney out: £4,118.33\nClosing: £4,327.34\n\nTop spending:\n1. Bills: £1,245.00\n2. Groceries: £892.45\n3. Transport: £567.20\n\nView in app.\n\nMonzo Bank Ltd",
            ["statement", "monthly", "no_action"],
        ),
        (
            "HMRC: Your Self Assessment tax return reminder",
            "Dear James Hinton,\n\nReminder: Self Assessment for 2024-2025 tax year.\n\nOnline filing deadline: 31 January 2026 (PASSED)\n\nIf not filed, penalties apply:\n- Initial: £100\n- Daily: £10/day after 3 months\n\nFile: gov.uk/self-assessment-tax-returns\n\nHM Revenue & Customs",
            ["tax", "deadline_passed", "penalty", "urgent"],
        ),
        (
            "AWS billing alert: Unusual spend detected",
            "AWS Billing Alert\n\nAccount: 1234-5678-9012\n\nCurrent charges: ${amount}\nForecast: ${forecast}\nThreshold: $500.00\n\nTop services:\n1. EC2: ${ec2}\n2. RDS: ${rds}\n3. S3: ${s3}\n\nReview: console.aws.amazon.com/billing/\n\nAWS",
            ["cloud_billing", "alert", "overspend"],
        ),
        (
            "Payment received - Thank you",
            "Hi James,\n\nWe've received your payment of £5,400.00 for invoice INV-2026-0847.\n\nPayment date: February 14, 2026\nMethod: Bank transfer\nReference: INV-2026-0847\n\nThank you for your prompt payment.\n\nAccounts Team\nCatalyst Solutions Ltd",
            ["payment_confirmation", "no_action"],
        ),
        (
            "Suspicious transaction on your account",
            "Security Alert\n\nWe've detected an unusual transaction on your account:\n\nAmount: £847.00\nMerchant: CRYPTO-EXCHANGE.COM\nDate: February 14, 2026, 03:42 AM\n\nWas this you?\n\n✅ Yes, this was me\n❌ No, block my card\n\nIf this wasn't you, your card will be frozen immediately.\n\nBarclays Security Team",
            ["fraud_alert", "urgent", "action_required"],
        ),
        (
            "Your quarterly VAT return is due",
            "Dear James Hinton,\n\nYour VAT return for Q4 2025 (Oct-Dec) is due by February 7, 2026.\n\nSubmit via your Government Gateway account.\n\nLate filing may result in penalties.\n\nHMRC",
            ["tax", "deadline", "compliance"],
        ),
    ]


def _calendar_invite_templates():
    return [
        (
            "Calendar: Team Standup - Daily at 9:30 AM",
            "You have been invited:\n\nTeam Standup\nWhen: Mon-Fri, 9:30 AM - 9:45 AM (GMT)\nWhere: Google Meet\nOrganizer: {organizer}\n\nNotes: Quick 15-min daily sync. Camera optional.\n\nGoing? Yes - Maybe - No",
            ["recurring", "standup"],
        ),
        (
            "Invitation: Architecture Review - Microservices Migration",
            "You have been invited:\n\nArchitecture Review\nWhen: Wednesday, February 19, 2:00 PM - 3:30 PM\nWhere: Conference Room B / Google Meet\n\nAgenda:\n1. Current monolith pain points\n2. Proposed service boundaries\n3. Data migration strategy\n4. Timeline and resource allocation\n\nPrepare: Analysis of authentication service.\n\nGoing? Yes - Maybe - No",
            ["one_off", "preparation_needed", "agenda"],
        ),
        (
            "Updated invitation: Sprint Demo moved to 4pm",
            "The following event has been updated:\n\nSprint Demo (was 2pm, now 4pm)\nWhen: Friday, February 14, 4:00 PM - 5:00 PM\nWhere: Main Conference Room\n\nNote from organizer: Moved to accommodate client call.\n\nGoing? Yes - Maybe - No",
            ["reschedule", "update"],
        ),
        (
            "Cancelled: Design Review",
            "The following event has been cancelled:\n\nDesign Review\nWas: Thursday, February 20, 11:00 AM\n\nNote from organizer: Postponed until next week due to design team availability.\n\nGoogle Calendar",
            ["cancellation", "info_only"],
        ),
    ]


def _social_templates():
    return [
        (
            "LinkedIn: 5 people viewed your profile",
            "James, 5 people viewed your profile this week\n\n1. Recruiter at Google\n2. CTO at TechStartup Inc\n3. Engineering Manager at Meta\n4. 2 others\n\nProfile rank: Top 8%\n\nLinkedIn",
            ["notification", "vanity_metric"],
        ),
        (
            "New follower on GitHub",
            "Hey jameshinton,\n\ntechdev2026 is now following you.\n\nProfile: github.com/techdev2026\nRepos: 23 | Followers: 156\n\nGitHub",
            ["notification", "social", "minimal"],
        ),
        (
            "Congrats! You've earned a badge on Stack Overflow",
            "You've earned the \"Enlightened\" gold badge.\n\nAwarded for: answer accepted with score 10+\nQuestion: \"How to implement retry logic with exponential backoff in Python asyncio\"\nScore: 15 upvotes\n\nStack Overflow",
            ["achievement", "gamification"],
        ),
        (
            "You're invited: London Python Meetup",
            "London Python Meetup - February Edition\n\nDate: Thursday, February 20, 6:30 PM\nVenue: WeWork Moorgate, London EC2\n\nSpeakers:\n1. \"Building Production RAG Systems with LangChain\"\n2. \"Type Safety in Modern Python: Beyond mypy\"\n\nFree pizza and drinks.\n\nRSVP: meetup.com/london-python\n127 attending · 45 spots left",
            ["event", "community", "rsvp"],
        ),
        (
            "Someone mentioned you on Twitter",
            "@techdev2026 mentioned you:\n\n\"Just discovered @jameshinton's open-source AI classification engine. Exactly what I needed for my email automation project! 🔥\"\n\n3 likes · 1 retweet\n\nView: twitter.com/techdev2026/status/12345\n\nTwitter/X",
            ["mention", "social", "positive"],
        ),
    ]


def _automated_templates():
    return [
        (
            "Your GitHub Actions workflow has failed",
            "Run failed: CI Pipeline\n\nRepo: jameshinton/zetherion-ai\nBranch: {branch}\nCommit: {commit_hash} - {commit_msg}\n\nFailed jobs:\n- test (Python 3.13) - Exit code 1\n  Error: {test_error}\n\nView: github.com/jameshinton/zetherion-ai/actions/runs/{run_id}",
            ["ci_failure", "action_needed"],
        ),
        (
            "Your Vercel deployment failed",
            "Deployment Failed\n\nProject: zetherion-landing\nBranch: main\nCommit: {commit_msg}\n\nBuild Error:\n{build_error}\n\nView logs: vercel.com/jameshinton/zetherion-landing/deployments\n\nVercel",
            ["deploy_failure", "action_needed"],
        ),
        (
            "Sentry Alert: {error_count} new errors in zetherion-api",
            "New Issue in zetherion-api\n\nError: {error_type}\nFirst seen: {time_ago}\nEvents: {error_count}\nUsers affected: {users}\n\nStack trace:\n  File \"{file}\", line {line}\n    {code_line}\n\nView: sentry.io/organizations/zetherion/issues/{issue_id}/\n\nSentry",
            ["error_alert", "monitoring", "action_needed"],
        ),
        (
            "Digital Ocean: Droplet running low on disk space",
            "Disk Space Warning\n\nDroplet: prod-zetherion-01\nUsage: {usage}% ({used}GB / {total}GB)\nProjected full: ~{days} days\n\nRecommended:\n1. docker system prune\n2. Rotate log files\n3. Resize droplet\n\nDigitalOcean",
            ["infrastructure", "warning", "action_needed"],
        ),
        (
            "Cloudflare: DDoS attack mitigated",
            "DDoS Attack Mitigated\n\nDomain: zetherion.com\nType: HTTP Flood (Layer 7)\nDuration: {duration} minutes\nPeak: {peak} req/s\nBlocked: {blocked} requests\nOrigin impact: None\n\nStatus: Resolved\nNo action required.\n\nCloudflare",
            ["security", "mitigated", "info_only"],
        ),
        (
            "GitHub Security Alert: Vulnerability in dependency",
            "Dependabot Alert\n\nRepo: jameshinton/zetherion-ai\nSeverity: {severity}\n\nVulnerability: {package} < {fixed_version}\nCVE: CVE-2026-{cve_num}\nDescription: {vuln_desc}\n\nFix: Update to >= {fixed_version}\n\nGitHub Security",
            ["security_vuln", "action_needed"],
        ),
        (
            "Docker Hub: Image pushed successfully",
            "Image pushed:\n\nRepo: jameshinton/zetherion-ai\nTag: v{version}\nDigest: sha256:{digest}\nSize: {size} MB\nPlatform: linux/amd64, linux/arm64\n\nDocker",
            ["ci_success", "info_only"],
        ),
        (
            "Slack summary: {unread_count} unread messages",
            "You have {unread_count} unread messages\n\n#engineering ({eng_count} new)\n- {person1}: {msg1}\n- {person2}: {msg2}\n\n#random ({random_count} new)\n- Various messages\n\n#incidents ({inc_count} new)\n- ops-bot: {incident_msg}\n\nCatch up in Slack\n\nSlack",
            ["digest", "notification", "no_action"],
        ),
        (
            "Suspicious login attempt on your Google account",
            "Someone used your password to sign in.\n\nDate: {date}\nLocation: {location}\nDevice: Unknown\nIP: {ip}\n\nGoogle blocked this attempt.\n\nReview: myaccount.google.com/notifications\n\nGoogle Accounts",
            ["security_alert", "urgent", "action_recommended"],
        ),
        (
            "Your cron job failed: backup-daily",
            "Cron Job Failure\n\nJob: backup-daily\nServer: prod-zetherion-01\nExit code: 1\nOutput:\n  ERROR: pg_dump failed - connection refused\n  ERROR: PostgreSQL not responding on port 5432\n\nLast successful: 2 days ago\n\nCrontab Monitor",
            ["cron_failure", "action_needed", "database"],
        ),
        (
            "SSL certificate expiring in 7 days",
            "Warning: SSL Certificate Expiry\n\nDomain: api.zetherion.com\nExpires: February 21, 2026\nDays remaining: 7\n\nAction required: Renew or enable auto-renewal.\n\nIf using Let's Encrypt, check certbot renewal.\n\nSSL Monitor",
            ["ssl_expiry", "urgent", "action_needed"],
        ),
    ]


def _recruitment_templates():
    return [
        (
            "Exciting opportunity: Senior Backend Engineer at {company}",
            "Hi James,\n\nImpressed by your Python/distributed systems experience. Perfect fit for our Senior Backend Engineer role.\n\nRole:\n- Lead backend architecture for {product}\n- Python, {tech_stack}\n- Salary: £{salary_min}k-£{salary_max}k + equity\n- {work_model} from {location}\n\n15-minute chat to discuss?\n\n{name}\n{title}, {company}",
            ["cold_outreach", "tech_role", "salary"],
        ),
        (
            "Following up on my previous email",
            "Hi James,\n\nJust bumping this to the top of your inbox. The role I mentioned last week is still open and I think you'd be a great fit.\n\nWould you be open to a brief call this week?\n\nBest,\n{name}",
            ["follow_up", "persistent", "minimal"],
        ),
        (
            "Invitation to interview - {company}",
            "Hi James,\n\nThank you for your application for the {role} position at {company}.\n\nWe'd like to invite you to a technical interview:\n\nDate: {date}\nTime: {time}\nFormat: {format}\nDuration: {duration}\n\nPlease confirm your availability.\n\n{name}\nTalent Acquisition, {company}",
            ["interview_invite", "action_required", "scheduling"],
        ),
        (
            "We'd love your feedback on our hiring process",
            "Hi James,\n\nThank you for interviewing with us. While we've decided to move forward with another candidate, we were impressed by your skills.\n\nWe'd appreciate your feedback on our hiring process:\n[Survey link]\n\nWe'll keep your profile on file for future opportunities.\n\nBest,\n{name}\n{company}",
            ["rejection", "feedback_request", "polite"],
        ),
    ]


# Combine all template generators
CATEGORY_TEMPLATES = {
    "personal": _personal_templates,
    "work_colleague": _work_colleague_templates,
    "work_client": _work_client_templates,
    "work_vendor": _work_vendor_templates,
    "transactional": _transactional_templates,
    "newsletter": _newsletter_templates,
    "marketing": _marketing_templates,
    "support_inbound": _support_inbound_templates,
    "support_outbound": _support_outbound_templates,
    "financial": _financial_templates,
    "calendar_invite": _calendar_invite_templates,
    "social": _social_templates,
    "automated": _automated_templates,
    "recruitment": _recruitment_templates,
}

# Distribution weights (realistic inbox proportions)
CATEGORY_WEIGHTS = {
    "personal": 8,
    "work_colleague": 16,
    "work_client": 10,
    "work_vendor": 5,
    "transactional": 15,
    "newsletter": 8,
    "marketing": 10,
    "support_inbound": 4,
    "support_outbound": 2,
    "financial": 5,
    "calendar_invite": 4,
    "social": 4,
    "automated": 12,
    "recruitment": 5,
}

# Edge case emails that don't fit neatly into one category
AMBIGUOUS_TEMPLATES = [
    {
        "subject": "Re: Quick question",
        "body": "Yes.",
        "from_name": "Unknown",
        "from_email": "j.smith@company.com",
        "tags": ["ultra_minimal", "ambiguous", "no_context"],
    },
    {
        "subject": "",
        "body": "",
        "from_name": "",
        "from_email": "unknown@test.com",
        "tags": ["empty_everything"],
    },
    {
        "subject": "Fwd: Fwd: Fwd: Fwd: Fwd: MUST READ!!!",
        "body": "James you HAVE to see this!!!\n\n>>>>>>>>\nOriginal message lost in forwarding chain...\n\nSent from my iPhone",
        "from_name": "Tom Wilson",
        "from_email": "tom.wilson@gmail.com",
        "tags": ["chain_forward", "spam_like", "personal"],
    },
    {
        "subject": "Offre spéciale pour vous - 50% de réduction",
        "body": "Cher James,\n\nNous sommes ravis de vous offrir une réduction exclusive de 50% sur nos services premium.\n\nCliquez ici pour en profiter: https://example.fr/offre\n\nCordialement,\nL'équipe Marketing",
        "from_name": "Marketing Team",
        "from_email": "promo@frenchcompany.fr",
        "tags": ["non_english", "french", "marketing"],
    },
    {
        "subject": "お知らせ: アカウント確認が必要です",
        "body": "James様、\n\nアカウントのセキュリティ確認が必要です。以下のリンクからログインしてください。\n\nhttps://example.jp/verify\n\nよろしくお願いいたします。",
        "from_name": "Account Service",
        "from_email": "noreply@service.jp",
        "tags": ["non_english", "japanese", "phishing_like"],
    },
    {
        "subject": "PLEASE RESPOND ASAP!!!!",
        "body": "JAMES\n\nI NEED THE REPORT BY END OF DAY. THIS IS THE THIRD TIME I'M ASKING.\n\nTHIS IS UNACCEPTABLE.\n\n- MANAGEMENT",
        "from_name": "Unknown Manager",
        "from_email": "manager@company.com",
        "tags": ["all_caps", "aggressive", "urgency_real", "ambiguous_sender"],
    },
    {
        "subject": "Test",
        "body": "test",
        "from_name": "James Hinton",
        "from_email": "james@zetherion.com",
        "tags": ["self_sent", "test_email"],
    },
    {
        "subject": "Out of Office Re: Project Update",
        "body": "I am currently out of the office with limited access to email.\n\nI will return on Monday, February 24.\n\nFor urgent matters, please contact Lisa Patel at lisa.patel@acmecorp.com.\n\nThank you,\nMike Johnson",
        "from_name": "Mike Johnson",
        "from_email": "mike.johnson@acmecorp.com",
        "tags": ["ooo_auto", "auto_reply"],
    },
    {
        "subject": "Delivery Status Notification (Delay)",
        "body": "This is an automatically generated Delivery Status Notification.\n\nDelivery to the following recipients has been delayed:\n\n    client@bigcorp.com\n\nMessage will be retried for 48 hours.\n\nReporting-MTA: dns; mail.zetherion.com",
        "from_name": "",
        "from_email": "postmaster@zetherion.com",
        "tags": ["system_notification", "delivery_delay"],
    },
    {
        "subject": "Re: (no subject)",
        "body": "Sounds good 👍\n\nSent from my iPhone",
        "from_name": "Tom Wilson",
        "from_email": "tom.wilson@gmail.com",
        "tags": ["mobile", "minimal", "no_subject"],
    },
    {
        "subject": "⚡ BREAKING: Major security breach at CloudProvider ⚡",
        "body": "URGENT SECURITY ADVISORY\n\nCloudProvider has confirmed a data breach affecting enterprise customers.\n\nAffected services: Object Storage, Database-as-a-Service\nTimeframe: Jan 15 - Feb 10, 2026\nData exposed: API keys, customer metadata\n\nImmediate actions:\n1. Rotate all API keys\n2. Review access logs\n3. Enable MFA on all accounts\n\nMore info: cloudprovider.com/security-advisory-2026-02\n\nThis is a legitimate security advisory, not phishing.",
        "from_name": "CloudProvider Security",
        "from_email": "security@cloudprovider.com",
        "tags": ["security_advisory", "urgent", "action_needed", "could_be_phishing"],
    },
    {
        "subject": "CONGRATULATIONS! You've Won £1,000,000!!!",
        "body": "Dear Valued Customer,\n\nYou have been selected as the winner of our annual lottery!\n\nPrize: £1,000,000.00\nRef: UK/WIN/2026/482910\n\nTo claim, send your bank details to: claims@totallylegit.com\n\nACT NOW - Offer expires in 24 hours!\n\nLottery Commission",
        "from_name": "UK Lottery Commission",
        "from_email": "winner@lottery-scam.com",
        "tags": ["spam", "scam", "phishing"],
    },
    {
        "subject": "Meeting in 15 minutes - are you joining?",
        "body": "Hey James, we're about to start the design review. Are you planning to join? We need your input on the API contract.\n\n{name}",
        "from_name": "Lisa Patel",
        "from_email": "lisa.patel@acmecorp.com",
        "tags": ["time_sensitive", "immediate", "question"],
    },
    {
        "subject": "I'm sorry",
        "body": "James,\n\nI want to apologize for how I handled the situation in yesterday's meeting. I shouldn't have called out your code in front of the whole team. That wasn't fair.\n\nCan we grab a coffee and talk it through?\n\nMike",
        "from_name": "Mike Johnson",
        "from_email": "mike.johnson@acmecorp.com",
        "tags": ["emotional", "interpersonal", "apology"],
    },
    {
        "subject": "Fw: Confidential - Do not forward",
        "body": "FYI - see below. Don't share this further.\n\n--- Forwarded ---\nFrom: ceo@acmecorp.com\n\nTeam leads only:\n\nWe will be announcing a 15% headcount reduction next week. Please do not share this information. We will have a company-wide meeting on Monday.\n\nRobert",
        "from_name": "Sarah Chen",
        "from_email": "sarah.chen@acmecorp.com",
        "tags": ["confidential", "sensitive", "layoffs", "forwarded"],
    },
    {
        "subject": "GitHub Copilot: Your monthly usage report",
        "body": "Your Copilot Usage - January 2026\n\nCode completions accepted: 1,247\nChat messages: 89\nCode reviews assisted: 12\nLanguages: Python (68%), TypeScript (24%), Go (8%)\n\nYou're in the top 15% of Copilot users.\n\nView full report: github.com/settings/copilot\n\nGitHub",
        "from_name": "",
        "from_email": "noreply@github.com",
        "tags": ["usage_report", "automated", "stats"],
    },
    {
        "subject": "Invoice + Meeting request + Question about timeline",
        "body": "Hi James,\n\nThree things:\n\n1. Attached is our invoice for February (£2,800). Payment within 30 days please.\n\n2. Can we schedule a call next week to discuss the Q2 roadmap?\n\n3. The client is asking about the timeline for the API v2 migration. Can you give me a rough estimate?\n\nThanks,\nRachel",
        "from_name": "Rachel",
        "from_email": "vendor.design@creativestudio.io",
        "tags": ["multi_purpose", "invoice", "scheduling", "question"],
    },
]


def _random_date(days_back=30):
    """Random datetime within the last N days."""
    base = datetime.now(tz=timezone.utc)
    delta = timedelta(
        days=random.randint(0, days_back),
        hours=random.randint(0, 23),
        minutes=random.randint(0, 59),
    )
    return (base - delta).isoformat()


def _random_order_num():
    return f"{random.randint(100, 999)}-{random.randint(1000000, 9999999)}-{random.randint(1000000, 9999999)}"


def _fill_template(body: str) -> str:
    """Replace simple placeholders with random values."""
    contact = random.choice(KNOWN_CONTACTS)
    replacements = {
        "{name}": contact[0],
        "{name_2}": random.choice(KNOWN_CONTACTS)[0],
        "{title}": contact[2],
        "{company}": contact[3],
        "{ceo_email}": "ceo@acmecorp.com",
        "{date}": f"February {random.randint(1, 28)}, 2026",
        "{delivery_date}": f"February {random.randint(15, 28)}, 2026",
        "{order_num}": _random_order_num(),
        "{tracking}": "".join(random.choices(string.digits, k=9)) + "GB",
        "{item}": random.choice([
            "Logitech MX Master 3S Mouse",
            "Samsung T7 SSD 1TB",
            "Anker USB-C Hub",
            "Keychron K2 Keyboard",
            "CalDigit TS4 Thunderbolt Dock",
        ]),
        "{restaurant}": random.choice(["Wagamama - Soho", "Pizza Express", "Nando's Richmond", "Dishoom King's Cross"]),
        "{amount}": f"{random.uniform(12, 45):.2f}",
        "{fare}": f"{random.uniform(5, 25):.2f}",
        "{total}": f"{random.uniform(6, 30):.2f}",
        "{pickup}": random.choice(["Richmond Station", "Waterloo", "Kings Cross", "Home"]),
        "{dropoff}": random.choice(["42 High Street", "Office", "WeWork Moorgate", "Soho House"]),
        "{rx_num}": str(random.randint(10000, 99999)),
        "{ref}": "".join(random.choices(string.ascii_uppercase + string.digits, k=8)),
        "{branch}": random.choice(["feature/email-classification", "fix/auth-bug", "main", "develop"]),
        "{commit_hash}": "".join(random.choices("0123456789abcdef", k=7)),
        "{commit_msg}": random.choice(["Add email classification schema", "Fix auth token refresh", "Update dependencies", "Refactor tests"]),
        "{test_error}": random.choice([
            "test_classification_schema.py::TestTopicNormalisation::test_cap_at_10 FAILED",
            "test_agent_core.py::TestAgentLoop::test_timeout AssertionError",
            "test_gmail_client.py::test_fetch_messages ConnectionError",
        ]),
        "{run_id}": str(random.randint(100000, 999999)),
        "{build_error}": random.choice([
            "Module not found: Error: Can't resolve 'lottie-react'",
            "TypeError: Cannot read properties of undefined (reading 'map')",
            "ESLint: 3 errors found",
        ]),
        "{error_count}": str(random.randint(5, 500)),
        "{error_type}": random.choice([
            "ConnectionResetError: [Errno 104] Connection reset by peer",
            "TimeoutError: Request timed out after 30s",
            "ValueError: Invalid JSON response from API",
            "MemoryError: Cannot allocate 2GB buffer",
        ]),
        "{time_ago}": random.choice(["2 hours ago", "30 minutes ago", "1 day ago"]),
        "{users}": str(random.randint(1, 200)),
        "{file}": random.choice([
            "src/zetherion_ai/skills/gmail/client.py",
            "src/zetherion_ai/routing/email_router.py",
            "src/zetherion_ai/agent/core.py",
        ]),
        "{line}": str(random.randint(50, 500)),
        "{code_line}": "resp = await self._client.get(url, headers=headers)",
        "{issue_id}": str(random.randint(10000, 99999)),
        "{usage}": str(random.randint(75, 95)),
        "{used}": str(random.randint(35, 48)),
        "{total}": "50",
        "{days}": str(random.randint(3, 14)),
        "{duration}": str(random.randint(5, 45)),
        "{peak}": f"{random.randint(10, 100)},000",
        "{blocked}": f"{random.uniform(0.5, 5):.1f} million",
        "{severity}": random.choice(["High", "Critical", "Medium"]),
        "{package}": random.choice(["httpx", "pydantic", "cryptography", "pillow", "requests"]),
        "{fixed_version}": f"0.{random.randint(25, 35)}.{random.randint(0, 5)}",
        "{cve_num}": str(random.randint(10000, 99999)),
        "{vuln_desc}": random.choice([
            "HTTP/2 connection reuse vulnerability allows request smuggling",
            "Arbitrary code execution via crafted pickle payload",
            "SSRF via redirect following in URL validation",
        ]),
        "{version}": f"0.{random.randint(5, 8)}.{random.randint(0, 5)}",
        "{digest}": "".join(random.choices("0123456789abcdef", k=12)),
        "{size}": str(random.randint(150, 400)),
        "{unread_count}": str(random.randint(10, 100)),
        "{eng_count}": str(random.randint(5, 40)),
        "{random_count}": str(random.randint(3, 20)),
        "{inc_count}": str(random.randint(2, 15)),
        "{person1}": random.choice(KNOWN_CONTACTS)[0],
        "{person2}": random.choice(KNOWN_CONTACTS)[0],
        "{msg1}": random.choice(["Just pushed the fix", "Can someone review PR #248?", "Sprint retro at 4pm"]),
        "{msg2}": random.choice(["LGTM", "Merged!", "Need help with the migration"]),
        "{incident_msg}": random.choice(["INC-2847 resolved", "New incident: API latency spike", "Post-mortem Monday"]),
        "{location}": random.choice(["Lagos, Nigeria", "Moscow, Russia", "Unknown", "Shanghai, China"]),
        "{ip}": f"{random.randint(1,255)}.{random.randint(0,255)}.xx.xx",
        "{organizer}": random.choice(KNOWN_CONTACTS[:5])[1],
        "{product}": random.choice(["AI-powered fraud detection", "real-time analytics platform", "developer tools"]),
        "{tech_stack}": random.choice(["Go, Kubernetes", "FastAPI, PostgreSQL", "Django, Redis, Celery"]),
        "{salary_min}": str(random.randint(100, 140)),
        "{salary_max}": str(random.randint(150, 200)),
        "{work_model}": random.choice(["Hybrid", "Remote", "On-site"]),
        "{location}": random.choice(["London office (2 days/week)", "anywhere in UK", "San Francisco"]),
        "{role}": random.choice(["Senior Backend Engineer", "Staff Engineer", "Engineering Manager"]),
        "{time}": random.choice(["10:00 AM", "2:00 PM", "4:00 PM"]),
        "{format}": random.choice(["Video call (Google Meet)", "On-site", "Take-home + debrief"]),
        "{duration}": random.choice(["45 minutes", "1 hour", "2 hours"]),
        "{ec2}": f"${random.uniform(200, 600):.2f}",
        "{rds}": f"${random.uniform(100, 300):.2f}",
        "{s3}": f"${random.uniform(50, 150):.2f}",
        "{forecast}": f"${random.uniform(800, 1500):.2f}",
    }
    for key, value in replacements.items():
        body = body.replace(key, str(value))
    return body


def _pick_sender_for_category(category: str):
    """Pick an appropriate sender for the category."""
    if category in ("personal",):
        c = random.choice([c for c in KNOWN_CONTACTS if c[3] in ("", "Smile Dental")])
        return c[0], c[1]
    if category in ("work_colleague",):
        c = random.choice([c for c in KNOWN_CONTACTS if c[3] == "Acme Corp"])
        return c[0], c[1]
    if category in ("work_client",):
        c = random.choice([c for c in KNOWN_CONTACTS if c[3] in ("ClientCo", "StartupXYZ", "Tech Partners")])
        return c[0], c[1]
    if category in ("work_vendor",):
        c = random.choice([c for c in KNOWN_CONTACTS if c[3] in ("Creative Studio", "Design Agency", "Catalyst Solutions")])
        return c[0], c[1]
    if category in ("newsletter", "marketing"):
        sender = random.choice(MARKETING_SENDERS)
        return "", sender
    if category in ("transactional", "automated", "calendar_invite", "social", "support_outbound"):
        sender = random.choice(NOREPLY_SENDERS)
        return "", sender
    if category in ("recruitment",):
        r = random.choice(RECRUITER_SENDERS)
        return r[0], r[1]
    if category in ("financial",):
        return "", random.choice(["no-reply@monzo.com", "noreply@tax.service.gov.uk", "noreply@aws.amazon.com", "noreply@barclays.co.uk"])
    if category in ("support_inbound",):
        return random.choice(KNOWN_CONTACTS[:5])[0], random.choice(["support@stripe.com", "support@platform.com", "help@vendor.io"])
    # Fallback
    c = random.choice(KNOWN_CONTACTS)
    return c[0], c[1]


def generate_dataset(count: int) -> list[dict]:
    """Generate a diverse email dataset."""
    emails = []
    email_id_counter = 0

    # Calculate how many emails per category
    total_weight = sum(CATEGORY_WEIGHTS.values())
    category_counts = {}
    remaining = count

    # Reserve 5% for ambiguous/edge-case emails
    ambiguous_count = max(int(count * 0.05), len(AMBIGUOUS_TEMPLATES))
    category_email_count = count - ambiguous_count

    for cat, weight in CATEGORY_WEIGHTS.items():
        cat_count = int(category_email_count * weight / total_weight)
        category_counts[cat] = cat_count
        remaining -= cat_count

    # Distribute remainder
    for cat in list(category_counts.keys())[:remaining]:
        category_counts[cat] += 1

    # Generate category-based emails
    for category, target_count in category_counts.items():
        template_fn = CATEGORY_TEMPLATES.get(category)
        if not template_fn:
            continue
        templates = template_fn()

        for i in range(target_count):
            template = templates[i % len(templates)]
            subject_tmpl, body_tmpl, _tags = template

            subject = _fill_template(subject_tmpl)
            body = _fill_template(body_tmpl)
            sender_name, sender_email = _pick_sender_for_category(category)

            email_id_counter += 1
            emails.append({
                "email_id": f"gen_{email_id_counter:04d}",
                "subject": subject,
                "from_email": sender_email,
                "to_emails": [RECIPIENT],
                "body_text": body,
                "received_at": _random_date(),
                "thread_id": f"thread_{random.randint(1, 500)}",
                "expected_category": category,
            })

    # Add ambiguous/edge-case emails
    for i, tmpl in enumerate(AMBIGUOUS_TEMPLATES):
        if i >= ambiguous_count:
            break
        email_id_counter += 1
        emails.append({
            "email_id": f"gen_{email_id_counter:04d}",
            "subject": _fill_template(tmpl["subject"]),
            "from_email": tmpl["from_email"],
            "to_emails": [RECIPIENT],
            "body_text": _fill_template(tmpl["body"]),
            "received_at": _random_date(),
            "thread_id": f"thread_{random.randint(1, 500)}",
            "expected_category": "ambiguous",
        })

    # Fill any remaining count with random category emails
    while len(emails) < count:
        cat = random.choice(list(CATEGORY_TEMPLATES.keys()))
        templates = CATEGORY_TEMPLATES[cat]()
        template = random.choice(templates)
        subject_tmpl, body_tmpl, _tags = template

        email_id_counter += 1
        sender_name, sender_email = _pick_sender_for_category(cat)
        emails.append({
            "email_id": f"gen_{email_id_counter:04d}",
            "subject": _fill_template(subject_tmpl),
            "from_email": sender_email,
            "to_emails": [RECIPIENT],
            "body_text": _fill_template(body_tmpl),
            "received_at": _random_date(),
            "thread_id": f"thread_{random.randint(1, 500)}",
            "expected_category": cat,
        })

    # Shuffle for realistic ordering
    random.shuffle(emails)

    return emails


def main():
    parser = argparse.ArgumentParser(description="Generate synthetic email dataset")
    parser.add_argument("--count", type=int, default=1000, help="Number of emails")
    parser.add_argument(
        "--output",
        default="benchmarks/datasets/synthetic_1000.json",
        help="Output file path",
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    args = parser.parse_args()

    random.seed(args.seed)

    emails = generate_dataset(args.count)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    dataset = {
        "metadata": {
            "source": "generated",
            "created_at": datetime.now(tz=timezone.utc).isoformat(),
            "description": f"{len(emails)} synthetic emails across all 15 categories with edge cases",
            "count": len(emails),
            "seed": args.seed,
            "category_distribution": {},
        },
        "emails": emails,
    }

    output_path.write_text(json.dumps(dataset, indent=2))
    print(f"Generated {len(emails)} emails to {output_path}")

    # Print category distribution estimate
    print(f"\nCategory weight distribution:")
    total_w = sum(CATEGORY_WEIGHTS.values())
    for cat, w in sorted(CATEGORY_WEIGHTS.items(), key=lambda x: -x[1]):
        expected = int(args.count * 0.95 * w / total_w)
        print(f"  {cat:<20} ~{expected} emails ({w/total_w*100:.0f}%)")
    print(f"  {'ambiguous/edge':<20} ~{int(args.count * 0.05)} emails (5%)")


if __name__ == "__main__":
    main()
