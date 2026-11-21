# AgentGuard public website kit

Create a site in **Hashmeta AI CMS** in the team's workspace. This is the page copy and a reference layout for that site. Publishing requires the team's CMS account, a public HTTPS demo URL and an approved business contact. Keep API keys, local paths and private credentials off the public page. The localhost service is intended for a single-machine demonstration. Use a separately secured sandbox for a public demo, or supply a walkthrough of your running demo as the public CTA. Record the CMS-generated URL after publish.

## Home

**Give your travel agent room to negotiate. Keep control of the offer.**

When a customer wants to move a booking, the price of saying yes can depend on what the supplier does next. AgentGuard checks the proposal before the agency promises a date or a price.

Primary button **Explore the demo** → team's demo or walkthrough URL. Secondary link **How decisions work** → the explanation section.

## Travel case study

**One request. Three possible responses.**

A traveller asks to move a trip by 30 days at no extra charge. The agency can decline, guarantee the change immediately, or ask the supplier for a binding hold. In the reference example, waiting for the hold improves expected value by SGD 56 over declining. A guarantee exposes the agency to a rare, costly failure.

Northline Travel is fictional. The use case was inspired by Mesho Travel & Tours' customer-acquisition story. All bookings, costs, supplier statistics and loss amounts in this demonstration are synthetic.

## How decisions work

1. The traveller confirms a new itinerary in the chat. A connected QwenWork sales worker proposes alternatives for a saved negotiation request.
2. The backend composes an explicit model from the active booking and approved assumptions. PAT evaluates the same economic-utility query for each alternative when configured and calibrated.
3. The customer can ask why an offer was made and see the exact approved terms in the same chat. A simulated supplier hold controls whether a conditional booking actually moves.

## Transparency

A model only covers the assumptions and outcomes it contains. Travellers can ask for the decision and the comparison behind their own offer. If evidence is incomplete or PAT is unavailable, the customer request goes to review. Standard demo reservations can be created in preview mode. Negotiated changes require calibrated PAT and customer consent. No live payments or supplier changes occur.

## Contact

Use the CMS contact block with the owner's approved address or form. Link the public CTA only after testing it in a signed-out browser. Keep negotiations and private founder records off the public site.

## Build and publication

Use the headline and subtitle in the CMS hero, the travel story in a case-study section, and the three steps and limits beneath it. Use the blue, navy and white palette of the customer desk. Set the CMS preview page title to “AgentGuard | Governed travel negotiations”. Check mobile layout and the external CTA, publish to the team's chosen domain, then record the actual URL and publish date in the submission. Do not present a local HTML render as a published Hashmeta site.
