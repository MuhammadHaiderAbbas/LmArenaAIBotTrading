# Compliance & regulatory notes (UK)

**This document is an issue map, not legal advice.** It records the questions that must be answered
with specialist counsel before AUREX AI moves beyond private development with virtual capital.

## Current posture

| Dimension | Phase 1 position |
| --- | --- |
| Users | Private development only; no public sign-up for a live service |
| Capital | Virtual only — no customer money is accepted or held |
| Execution | Paper simulator only; no broker or exchange connection exists |
| Advice | None given; the platform is a development environment |
| Data | Only account data created by testers; no purchased market data yet |

## Staged plan

### Stage 1 — Private R&D (current)
Own environment, virtual capital, paper trading, no customer money. Keep the product in a
non-customer, non-live-money development scope and assess any external research or advice activity
before offering it.

### Stage 2 — Research SaaS
User accounts, paid research software, market information and customer paper trading. Obtain
specialist advice on whether features, recommendations, promotions or execution links cross a
regulated perimeter.

### Stage 3 — Live trading services
Broker execution, automated customer investment management, funding and withdrawals. Do not launch
until permissions, authorised partners, custody/client-money arrangements, compliance and operational
controls are established.

## Issues to assess with specialist counsel

- Financial Services and Markets Act 2000 and FCA regulated activities.
- Dealing, arranging, advising and discretionary portfolio management permissions where applicable.
- Client money, custody and safeguarding obligations (CASS).
- Financial promotions and marketing restrictions, including performance claims.
- KYC, AML, sanctions screening and fraud controls.
- Consumer Duty and complaints handling where applicable.
- UK GDPR and Data Protection Act 2018, plus cybersecurity expectations.
- Cryptoasset-specific registration/authorisation and financial-promotion rules. The FCA's new UK
  cryptoasset regime is scheduled to take effect on **25 October 2027**, with an application window
  from 30 September 2026 to 28 February 2027. Applicability and transitional rules must be checked
  against actual proposed activities and current FCA guidance.
- Market-data licensing, exchange terms and intellectual-property rights.

## Hard rules encoded in the product

1. A payment gateway alone does **not** authorise investment services or client-money handling.
2. Integrate with established regulated brokers/custodians rather than holding customer assets.
3. No performance promise, projection or "guaranteed return" language appears anywhere in the product.
4. Paper balances are labelled non-withdrawable at every point of display.
5. Live trading and payment features are gated behind configuration that is `false` and behind a
   separate legal, security and operational review.

## Official starting points

- FCA authorisation: <https://www.fca.org.uk/firms/authorisation/apply>
- FCA cryptoasset regime: <https://www.fca.org.uk/firms/new-regime-cryptoasset-regulation>
- FCA cryptoasset financial promotions: <https://www.fca.org.uk/firms/cryptoasset-financial-promotions>

## Data protection considerations for Phase 3+

- Lawful basis per processing activity; data-minimisation review before storing research documents.
- Processor agreements with every market-data, news, LLM and broker provider.
- Retention schedule for audit records, research artefacts and model versions.
- Data-subject request handling across the ledger and audit trail (append-only logs need a documented
  approach that does not break financial integrity).
- Data residency review before choosing hosting regions for UK users.
