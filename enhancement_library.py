"""Hardcoded enhancement library, keyed by business_type then business_stage.

Used by agents/enhancement_suggester.py to surface standard-for-this-stage
suggestions. Stage keys match business_stage values used across onboarding:
"Foundation" (idea/validated), "Build" (building), "Launch" (launched),
"Revenue" (launched + monetising), "Scale" (established).

Each entry: {enhancement, reason, priority, effort}.
priority: urgent | standard | nice_to_have
effort: low | medium | high
"""

ENHANCEMENT_LIBRARY = {
    "SaaS": {
        "Foundation": [
            {"enhancement": "Landing page with an email waitlist", "reason": "Validates interest before any code is written.", "priority": "standard", "effort": "low"},
            {"enhancement": "5-10 customer discovery calls", "reason": "Standard before writing a line of product code.", "priority": "urgent", "effort": "medium"},
            {"enhancement": "Competitor teardown doc", "reason": "Clarifies what 'better' actually means for this market.", "priority": "standard", "effort": "low"},
            {"enhancement": "One-sentence value proposition test", "reason": "If it can't be said in one sentence, it isn't validated yet.", "priority": "standard", "effort": "low"},
            {"enhancement": "Pricing hypothesis document", "reason": "Standard to have a pricing guess before building billing.", "priority": "nice_to_have", "effort": "low"},
        ],
        "Build": [
            {"enhancement": "Error monitoring (Sentry free tier)", "reason": "Standard from first deploy — silent bugs cost trust.", "priority": "standard", "effort": "low"},
            {"enhancement": "Basic analytics (PostHog or Mixpanel)", "reason": "Standard before launch to know what's actually used.", "priority": "standard", "effort": "low"},
            {"enhancement": "Staging environment separate from production", "reason": "Standard once real user data exists.", "priority": "standard", "effort": "medium"},
            {"enhancement": "Automated daily database backups", "reason": "Standard before any real user data is stored.", "priority": "urgent", "effort": "low"},
            {"enhancement": "Terms of service and privacy policy", "reason": "Standard before collecting any user data.", "priority": "urgent", "effort": "low"},
            {"enhancement": "In-app feedback widget", "reason": "Standard to catch friction before public launch.", "priority": "nice_to_have", "effort": "low"},
        ],
        "Launch": [
            {"enhancement": "Email onboarding sequence (3-5 emails)", "reason": "Standard at first paying user for retention.", "priority": "urgent", "effort": "medium"},
            {"enhancement": "Subscription billing (Stripe billing)", "reason": "Standard once monetisation starts.", "priority": "urgent", "effort": "medium"},
            {"enhancement": "Status page", "reason": "Standard once users depend on uptime.", "priority": "standard", "effort": "low"},
            {"enhancement": "Referral mechanism", "reason": "Standard once product-market fit signal appears.", "priority": "nice_to_have", "effort": "medium"},
            {"enhancement": "Social proof section on landing page", "reason": "Standard after first handful of happy users.", "priority": "standard", "effort": "low"},
        ],
        "Revenue": [
            {"enhancement": "Churn analysis dashboard", "reason": "Standard once recurring revenue exists.", "priority": "standard", "effort": "medium"},
            {"enhancement": "A/B testing capability", "reason": "Standard at 100+ users to make evidence-based decisions.", "priority": "standard", "effort": "medium"},
            {"enhancement": "Case study documentation", "reason": "Standard after first paying success story.", "priority": "nice_to_have", "effort": "low"},
            {"enhancement": "Customer support ticketing system", "reason": "Standard once support volume exceeds email.", "priority": "standard", "effort": "medium"},
            {"enhancement": "Usage-based upsell prompts", "reason": "Standard to convert free/low-tier users at the right moment.", "priority": "nice_to_have", "effort": "medium"},
        ],
        "Scale": [
            {"enhancement": "SOC 2 / security review", "reason": "Standard once enterprise deals require it.", "priority": "standard", "effort": "high"},
            {"enhancement": "Multi-region infrastructure", "reason": "Standard once latency or compliance requires it.", "priority": "nice_to_have", "effort": "high"},
            {"enhancement": "Dedicated customer success function", "reason": "Standard once account count outpaces founder bandwidth.", "priority": "standard", "effort": "high"},
            {"enhancement": "Partner/integration ecosystem", "reason": "Standard for durable growth at scale.", "priority": "nice_to_have", "effort": "high"},
        ],
    },
    "E-commerce": {
        "Foundation": [
            {"enhancement": "Supplier/manufacturer vetting doc", "reason": "Standard before committing to inventory.", "priority": "urgent", "effort": "medium"},
            {"enhancement": "Landing page pre-order test", "reason": "Validates demand before inventory spend.", "priority": "standard", "effort": "low"},
            {"enhancement": "Unit economics spreadsheet", "reason": "Standard before pricing any product.", "priority": "urgent", "effort": "low"},
            {"enhancement": "Brand name and basic identity", "reason": "Standard before any paid marketing.", "priority": "standard", "effort": "low"},
        ],
        "Build": [
            {"enhancement": "Return policy page", "reason": "Standard before accepting first order.", "priority": "urgent", "effort": "low"},
            {"enhancement": "Inventory alert system", "reason": "Standard before stock runs out unnoticed.", "priority": "standard", "effort": "medium"},
            {"enhancement": "Customer support ticketing", "reason": "Standard before order volume grows.", "priority": "standard", "effort": "low"},
            {"enhancement": "Basic shipping cost calculator", "reason": "Standard to avoid margin surprises at checkout.", "priority": "urgent", "effort": "medium"},
            {"enhancement": "Product photography plan", "reason": "Standard — conversion depends on it more than copy.", "priority": "standard", "effort": "medium"},
        ],
        "Launch": [
            {"enhancement": "Abandoned cart email sequence", "reason": "Standard highest-ROI email at launch.", "priority": "urgent", "effort": "medium"},
            {"enhancement": "Post-purchase review request flow", "reason": "Standard to build social proof from day one.", "priority": "standard", "effort": "low"},
            {"enhancement": "Basic analytics (GA4 or Shopify analytics)", "reason": "Standard to know which channels convert.", "priority": "standard", "effort": "low"},
            {"enhancement": "Return policy visible at checkout", "reason": "Standard — reduces cart abandonment from policy uncertainty.", "priority": "standard", "effort": "low"},
        ],
        "Revenue": [
            {"enhancement": "Repeat-purchase email flow", "reason": "Standard once first cohort of buyers exists.", "priority": "standard", "effort": "medium"},
            {"enhancement": "Loyalty or referral program", "reason": "Standard once CAC starts rising.", "priority": "nice_to_have", "effort": "medium"},
            {"enhancement": "Inventory forecasting", "reason": "Standard once sales become predictable enough to forecast.", "priority": "standard", "effort": "medium"},
            {"enhancement": "Customer segmentation for email", "reason": "Standard to raise email revenue per send.", "priority": "nice_to_have", "effort": "medium"},
        ],
        "Scale": [
            {"enhancement": "Multi-warehouse fulfillment", "reason": "Standard once order volume strains a single location.", "priority": "standard", "effort": "high"},
            {"enhancement": "Wholesale/B2B channel", "reason": "Standard growth unlock at this stage.", "priority": "nice_to_have", "effort": "high"},
            {"enhancement": "International shipping and duties handling", "reason": "Standard once domestic growth plateaus.", "priority": "nice_to_have", "effort": "high"},
        ],
    },
    "Content/Creator": {
        "Foundation": [
            {"enhancement": "Email list setup before first post", "reason": "Standard — owns the audience the platform doesn't.", "priority": "urgent", "effort": "low"},
            {"enhancement": "Content pillars document", "reason": "Standard before posting inconsistently across topics.", "priority": "standard", "effort": "low"},
            {"enhancement": "Posting cadence commitment", "reason": "Standard — consistency beats quality early on.", "priority": "standard", "effort": "low"},
        ],
        "Build": [
            {"enhancement": "Content calendar", "reason": "Standard for consistency once posting regularly.", "priority": "standard", "effort": "low"},
            {"enhancement": "Analytics dashboard for engagement metrics", "reason": "Standard to know what's actually resonating.", "priority": "standard", "effort": "low"},
            {"enhancement": "Repurposing workflow across platforms", "reason": "Standard to multiply output without multiplying effort.", "priority": "nice_to_have", "effort": "medium"},
        ],
        "Launch": [
            {"enhancement": "Media kit", "reason": "Standard before any brand deal outreach.", "priority": "urgent", "effort": "low"},
            {"enhancement": "Rate card", "reason": "Standard before negotiating first paid deal.", "priority": "standard", "effort": "low"},
            {"enhancement": "Email list segmentation", "reason": "Standard once list is large enough to segment.", "priority": "nice_to_have", "effort": "medium"},
        ],
        "Revenue": [
            {"enhancement": "Digital product or membership offer", "reason": "Standard diversification once brand deals are inconsistent.", "priority": "standard", "effort": "high"},
            {"enhancement": "Sponsorship pipeline tracker", "reason": "Standard once multiple deals run concurrently.", "priority": "nice_to_have", "effort": "low"},
            {"enhancement": "Case study of best-performing sponsored content", "reason": "Standard to command higher rates.", "priority": "nice_to_have", "effort": "low"},
        ],
        "Scale": [
            {"enhancement": "Small team (editor, VA) to sustain output", "reason": "Standard once solo output caps growth.", "priority": "standard", "effort": "high"},
            {"enhancement": "Owned platform (app, course site) outside social", "reason": "Standard once dependent entirely on one platform's algorithm.", "priority": "nice_to_have", "effort": "high"},
        ],
    },
    "Service/Agency": {
        "Foundation": [
            {"enhancement": "Clear service packages and scope doc", "reason": "Standard before first client conversation.", "priority": "urgent", "effort": "low"},
            {"enhancement": "Portfolio or case study of past work", "reason": "Standard even from adjacent or personal projects.", "priority": "standard", "effort": "low"},
            {"enhancement": "Simple contract/SOW template", "reason": "Standard before taking on any paid work.", "priority": "urgent", "effort": "low"},
        ],
        "Build": [
            {"enhancement": "Client onboarding checklist", "reason": "Standard to avoid scope confusion on first project.", "priority": "standard", "effort": "low"},
            {"enhancement": "Basic invoicing and payment terms", "reason": "Standard before first invoice goes out.", "priority": "urgent", "effort": "low"},
            {"enhancement": "Project management system for client work", "reason": "Standard once running more than one engagement.", "priority": "standard", "effort": "medium"},
        ],
        "Launch": [
            {"enhancement": "Testimonial/case study collection process", "reason": "Standard after first delivered project.", "priority": "standard", "effort": "low"},
            {"enhancement": "Referral ask built into offboarding", "reason": "Standard highest-ROI channel for agencies.", "priority": "standard", "effort": "low"},
            {"enhancement": "Retainer offer for repeat clients", "reason": "Standard once project-based revenue is unpredictable.", "priority": "nice_to_have", "effort": "medium"},
        ],
        "Revenue": [
            {"enhancement": "Standardised delivery process/SOPs", "reason": "Standard once taking on more than 2-3 clients at once.", "priority": "standard", "effort": "medium"},
            {"enhancement": "First hire or subcontractor", "reason": "Standard once founder is the bottleneck on delivery.", "priority": "standard", "effort": "high"},
            {"enhancement": "Pricing tier restructure (value-based over hourly)", "reason": "Standard once demand exceeds capacity.", "priority": "nice_to_have", "effort": "medium"},
        ],
        "Scale": [
            {"enhancement": "Account management layer", "reason": "Standard once client count outpaces founder relationships.", "priority": "standard", "effort": "high"},
            {"enhancement": "Productised service offering", "reason": "Standard to scale beyond linear hours-for-dollars.", "priority": "nice_to_have", "effort": "high"},
        ],
    },
    "Mobile App": {
        "Foundation": [
            {"enhancement": "Clickable prototype for user testing", "reason": "Standard before writing production code.", "priority": "urgent", "effort": "medium"},
            {"enhancement": "App store category and competitor research", "reason": "Standard before naming or designing the app.", "priority": "standard", "effort": "low"},
            {"enhancement": "Core retention loop hypothesis", "reason": "Standard — most apps fail on retention, not acquisition.", "priority": "urgent", "effort": "low"},
        ],
        "Build": [
            {"enhancement": "Crash reporting (Sentry/Firebase Crashlytics)", "reason": "Standard before first TestFlight/beta build.", "priority": "urgent", "effort": "low"},
            {"enhancement": "Analytics events for core actions", "reason": "Standard to measure activation and retention.", "priority": "standard", "effort": "medium"},
            {"enhancement": "App store listing assets (screenshots, copy)", "reason": "Standard before submission.", "priority": "standard", "effort": "medium"},
            {"enhancement": "Push notification opt-in flow", "reason": "Standard for re-engagement infrastructure.", "priority": "nice_to_have", "effort": "medium"},
        ],
        "Launch": [
            {"enhancement": "Onboarding flow optimised for time-to-value", "reason": "Standard — first session determines retention.", "priority": "urgent", "effort": "medium"},
            {"enhancement": "App store review prompt at the right moment", "reason": "Standard to build rating/social proof.", "priority": "standard", "effort": "low"},
            {"enhancement": "Retention cohort dashboard (D1/D7/D30)", "reason": "Standard metric every app needs post-launch.", "priority": "standard", "effort": "medium"},
        ],
        "Revenue": [
            {"enhancement": "In-app purchase or subscription paywall test", "reason": "Standard once monetising.", "priority": "standard", "effort": "medium"},
            {"enhancement": "A/B testing on paywall placement/copy", "reason": "Standard to optimise conversion at this stage.", "priority": "nice_to_have", "effort": "medium"},
        ],
        "Scale": [
            {"enhancement": "Localisation for top markets", "reason": "Standard once growth plateaus in home market.", "priority": "nice_to_have", "effort": "high"},
            {"enhancement": "Cross-platform expansion (iOS/Android parity)", "reason": "Standard once single-platform ceiling is reached.", "priority": "standard", "effort": "high"},
        ],
    },
    "Marketplace": {
        "Foundation": [
            {"enhancement": "Supply-side validation before demand-side build", "reason": "Standard sequence — marketplaces die from empty supply.", "priority": "urgent", "effort": "medium"},
            {"enhancement": "Manual/concierge matching MVP", "reason": "Standard to validate the match before building automation.", "priority": "standard", "effort": "low"},
            {"enhancement": "Take-rate hypothesis document", "reason": "Standard before building payment infrastructure.", "priority": "standard", "effort": "low"},
        ],
        "Build": [
            {"enhancement": "Trust and safety basics (reviews, verification)", "reason": "Standard before opening to public supply/demand.", "priority": "urgent", "effort": "medium"},
            {"enhancement": "Payment escrow or split-payment handling", "reason": "Standard before facilitating first real transaction.", "priority": "urgent", "effort": "high"},
            {"enhancement": "Dispute resolution process", "reason": "Standard before real money changes hands.", "priority": "standard", "effort": "medium"},
        ],
        "Launch": [
            {"enhancement": "Supply-side onboarding concierge", "reason": "Standard to keep early supply quality high.", "priority": "standard", "effort": "medium"},
            {"enhancement": "First-transaction incentive for both sides", "reason": "Standard to solve cold-start liquidity.", "priority": "standard", "effort": "medium"},
            {"enhancement": "Basic search/discovery ranking", "reason": "Standard once listing count exceeds a browsable page.", "priority": "nice_to_have", "effort": "medium"},
        ],
        "Revenue": [
            {"enhancement": "Take-rate optimisation by category", "reason": "Standard once transaction volume enables analysis.", "priority": "nice_to_have", "effort": "medium"},
            {"enhancement": "Repeat-transaction retention flow", "reason": "Standard once one-time buyers need to become repeat.", "priority": "standard", "effort": "medium"},
        ],
        "Scale": [
            {"enhancement": "Automated trust/fraud detection", "reason": "Standard once manual review can't keep pace with volume.", "priority": "standard", "effort": "high"},
            {"enhancement": "Category expansion playbook", "reason": "Standard once first category demonstrates liquidity.", "priority": "nice_to_have", "effort": "high"},
        ],
    },
}


def get_enhancements(business_type: str, business_stage: str) -> list:
    """Return the enhancement list for a business type/stage, or [] if unknown."""
    return ENHANCEMENT_LIBRARY.get(business_type, {}).get(business_stage, [])
