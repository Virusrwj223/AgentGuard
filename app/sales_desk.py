"""Sales intent, bounded candidate plans, and explanations grounded in the saved decision."""
import re
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction

from .contracts import Goal, Proposal
from .itinerary_revision import FARE_FIELDS, read_revision
from .trip_intake import DATE_RE, NUM, civil_date, money, number, seat_preference

LOWER_PRICE = r"\b(?:lower|reduce|bring down|cut)\s+(?:(?:the|my|our|this)\s+)?(?:total(?: cost| price)?|price|fare|cost)\b"
FREE_BOOKING = r"\b(?:make\s+(?:(?:the|my|our|this)\s+)?(?:total(?: cost| price)?|price|fare|booking|trip)\s+free|waive\s+(?:(?:the|my|our|this|entire|full)\s+)*(?:total(?: cost| price)?|price|fare))\b"
CREDIT = r"\b(?:discount|rebate|goodwill|cheaper|credit|deal on|money off|price match|price reduction)\b|(?:%|\bper\s*cent|\d|\bdollars?|\bbucks|\bSGD)\s*(?:off|discount)\b|\bSGD\s*[\d.,]+\s*off\b"
UPGRADE = r"\b(?:upgrad\w*|better room|business class)\b"
AMEND = r"\b(?:reschedul\w*|postpon\w*|push back|amend\w*|(?:move|shift|change)\b.{0,32}\b(?:days?|weeks?|date|trip|departure|booking)|(?:move|shift) (?:it|my trip)\b)"
CREDIT_INTENT = re.compile(f"(?:{CREDIT}|{LOWER_PRICE}|{FREE_BOOKING})", re.I)
NEG = re.compile(r"(?:" + CREDIT_INTENT.pattern + "|" + UPGRADE + "|" + AMEND +
                 r"|\b(?:negotiat\w*|loyalty|waive\w*)\b)", re.I)
MONEY = re.compile(r"(?:S\$|SGD\s*\$?|\$)\s*(\d+(?:,\d{3})*(?:\.\d{1,2})?)(?![\d.])|\b(\d+(?:\.\d{1,2})?)\s*(?:SGD|dollars?|bucks)\b",re.I)
PERCENT = re.compile(r"\b(\d+(?:\.\d{1,2})?)\s*(?:%|per\s*cent\b)",re.I)


def negotiation_text(message):
    hit = NEG.search(message)
    if not hit:
        return None
    # Isolate the negotiation clause where possible. Preserve currency/percent before "off".
    prefix = message[:hit.start()]
    boundaries = list(re.finditer(r"(?:[;.!?]\s+|,\s*(?=(?:and |also |can |could |with a |please ))|\s+and\s+(?=(?:can|could|would|please|I want|give|apply|move|upgrade|request|ask|a discount|a \d)))", prefix, re.I))
    return (message[boundaries[-1].end():] if boundaries else message).strip()[:1800]


def is_credit(message):
    return bool(CREDIT_INTENT.search(message))


def cents(amount):
    return int((Decimal(amount.replace(",","")) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def goal_from_message(message, booking):
    message = re.sub(r"\b("+NUM+r")(?=\s+(?:dollars?|bucks|per\s*cent|SGD)\b)",
                     lambda m: str(number(m[0])),message,flags=re.I)
    change = bool(re.search(AMEND, message, re.I))
    upgrade = bool(re.search(UPGRADE, message, re.I))
    free_booking = bool(re.search(FREE_BOOKING, message, re.I))
    credit = is_credit(message)
    if booking.get("self_service") and not re.search(r"\broom\b", message, re.I):
        revision, question = read_revision(message, booking)
        if question:
            return None, question
        if revision:
            if not (credit or MONEY.search(message) or PERCENT.search(message) or re.search(r"\b(?:free|no (?:extra |additional )?(?:charge|cost))\b", message, re.I)):
                return revision, None
            if revision.type == "amend" and not credit and not MONEY.search(message):
                return revision, None
            # Preserve explicit financial terms when the traveller edits several fields.
            if re.search(r"\b(?:USD|EUR|GBP|AUD|CAD|JPY)\b|US\$",message,re.I):
                return None, "What amount in SGD should I check for these changes?"
            if revision.type == "seat_preference":
                fields = {"seat_preference": revision.seat_preference}
            elif revision.type == "amend":
                fields = {key:str(date.fromisoformat(booking[key])+timedelta(days=revision.days))
                          for key in ("departure","return_date")}
            else:
                fields = revision.changes.model_dump(exclude_none=True)
            if credit:
                # Reuse the credit parser with financial language only, preserving the
                # same SGD and percentage basis used on standalone credit requests.
                amounts = list(MONEY.finditer(message))
                percentages = list(PERCENT.finditer(message))
                if len(amounts) + len(percentages) != 1:
                    return None, "What single credit amount or percentage should I include with the itinerary change?"
                token = (amounts or percentages)[0][0]
                financial, question = goal_from_message("credit " + token, booking)
                if question:
                    return None, question
                return Goal(type="itinerary",changes=fields,discount_cents=financial.discount_cents), None
            amounts = list(MONEY.finditer(message))
            if len(amounts)>1 or PERCENT.search(message):
                return None, "What is the maximum additional charge in SGD for this change?"
            maximum = cents(amounts[0][1] or amounts[0][2]) if amounts else 0
            if maximum > 100_000_000:
                return None, "Please specify a maximum additional charge within SGD 1,000,000."
            return Goal(type="itinerary", changes=fields,revision_charge_limit_cents=maximum), None
    preference = seat_preference(message)
    if preference:
        if credit or change or upgrade:
            return None, "Which should I check first: the seat preference or the other booking change?"
        if not booking.get("self_service"):
            return None, "This reservation does not support seat preferences."
        return Goal(type="seat_preference", seat_preference=preference), None
    if re.search(r"\bseats?\b", message, re.I):
        return None, "I can check a window, aisle, middle or sit-together preference for your demo booking. The airline handles individual seat assignments."
    amount_matches = list(MONEY.finditer(message))
    amounts = [cents(m[1] or m[2]) for m in amount_matches]
    percentages = list(PERCENT.finditer(message))
    if credit and not amounts and not percentages:
        plain = re.search(r"\b(\d+(?:\.\d{1,2})?)\s+off\b",message,re.I)
        if plain:
            amounts.append(cents(plain[1]))
    if re.search(r"\b(?:USD|EUR|THB|MYR|IDR|JPY|GBP|AUD)\b|US\$", message, re.I):
        return None, "This booking is priced in SGD. What amount in SGD would you like me to check?"
    if credit and (change or upgrade):
        return None, "I can check the discount and itinerary change separately. Which one would you like to check first?"
    if credit:
        if "paid_cents" not in booking:
            return None, "This older demo booking has no recorded price for a credit calculation. I can check a date change or room upgrade."
        if not amounts and not percentages and booking["paid_cents"] == 0:
            return None, "Your current booking total is already SGD 0.00."
        if not amounts and not percentages and free_booking:
            return Goal(type="credit", discount_cents=booking["paid_cents"]), None
        if not amounts and not percentages and "credit_headroom_cents" in booking and (
            re.search(LOWER_PRICE, message, re.I) or re.search(r"\bcheaper\b", message, re.I)
        ):
            # At zero headroom, check the smallest possible positive credit.
            # The compiler charges each additional cent plus all prior credit.
            requested = max(1, min(booking["paid_cents"], booking["credit_headroom_cents"]))
            return Goal(type="credit", discount_cents=requested), None
        if len(amounts) + len(percentages) != 1:
            return None, "What discount should I check? Give one figure such as “SGD 10 off” or “10% off”."
        if percentages:
            pct = Decimal(percentages[0][1])
            if not 0 < pct <= 100:
                return None, "Please choose a discount greater than 0% and at most 100%."
            amount = int((Decimal(booking["paid_cents"]) * pct / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        else:
            amount = amounts[0]
            if amount_matches and re.search(r"(?:price|fare|cost|total)\s+to\s*$", message[:amount_matches[0].start()], re.I):
                amount = booking["paid_cents"] - amount
        if amount <= 0 or amount > booking["paid_cents"]:
            return None, f"Choose a positive discount within the current {money(booking['paid_cents'])} booking total."
        return Goal(type="credit", discount_cents=amount), None
    if change or upgrade:
        charge = amounts[-1] if amounts and upgrade else 0
        if change and amounts and not upgrade and amounts[-1] > 0:
            return None, "I can check a date change at no extra charge. Please confirm if you want that option."
        if charge > 100_000_000:
            return None, "Please specify a smaller maximum upgrade charge in SGD."
        if change:
            offset = re.search(r"\b("+NUM+r")\s+(days?|weeks?)\s*(?:later|after|forward|back|out)?",message,re.I)
            d = number(offset[1]) * (7 if offset[2].lower().startswith("week") else 1) if offset else None
            if re.search(r"\b(?:earlier|before|sooner)\b", message, re.I) and d is not None:
                d = -d
            if d is None and (m := DATE_RE.search(message)):
                try:
                    start = date.fromisoformat(booking["departure"])
                    d = (civil_date(m[0], start) - start).days
                except (ValueError, OverflowError):
                    pass
            if not d or not -90 <= d <= 90 or str(d) not in booking["slots"]:
                return None, "That date is outside the available future slots. Please choose a date within 90 days of your departure."
            return Goal(type="amend_upgrade" if upgrade else "amend", days=d, max_extra_charge_cents=charge), None
        return Goal(type="upgrade",max_extra_charge_cents=charge), None
    return Goal(type="unknown"), None


def make_proposal(goal, booking, request_id):
    def item(name, op, **fields):
        return {"strategy_id":name,"plan":{"op":op,**fields}}
    candidates = []
    if goal.type == "itinerary":
        inventory = FARE_FIELDS.intersection(goal.changes.model_dump(exclude_none=True))
        candidates.append(item("revise_itinerary", "REVISE_ITINERARY" if inventory else "UPDATE_ITINERARY_DETAILS"))
    if goal.type == "seat_preference":
        candidates.append(item("record_seat_preference", "UPDATE_SEAT_PREFERENCE", seat_preference=goal.seat_preference))
    if goal.type == "credit":
        requested = goal.discount_cents
        candidates.append(item("requested_credit","ISSUE_TRIP_CREDIT",credit_cents=requested))
        alternative = booking.get("credit_headroom_cents",1000)
        if 0 < alternative < requested:
            candidates.append(item("smaller_credit","ISSUE_TRIP_CREDIT",credit_cents=alternative))
    if goal.type in ("amend","amend_upgrade","upgrade"):
        # Offer only registered upgrade charges. The governance engine selects the eligible plan.
        charges = booking.get("upgrade_prices_cents",[])
        alternatives = [v for v in charges if v > 0] or [0]
        within = [v for v in charges if v <= goal.max_extra_charge_cents]
        charge = max(within) if within and max(within)>0 else min(alternatives)
        if goal.type=="upgrade":
            candidates = [item("room_upgrade","UPGRADE",charge_cents=charge)]
        else:
            success = {"op":"CONFIRM_AMENDMENT"}
            if goal.type=="amend_upgrade":
                success = {"op":"SEQUENCE","steps":[success,{"op":"UPGRADE","charge_cents":charge}]}
            else:
                candidates.append(item("guarantee","GUARANTEE_AMENDMENT",days=goal.days))
            cond = {"op":"IF","predicate":"AMENDMENT_APPROVED","then":success,"otherwise":{"op":"KEEP_BOOKING"}}
            candidates.append(item("conditional","SEQUENCE",steps=[{"op":"REQUEST_AMENDMENT","days":goal.days},cond]))
    return Proposal.model_validate({"request_id":request_id,"booking_id":booking["booking_id"],
                                   "goal":goal.model_dump(),"needs_clarification":False,"candidate_strategies":candidates})


def explain(decision, execution):
    if not decision:
        return ("Your itinerary shows the advertised fare estimate or the simulated demo total. "
                "After you confirm the booking, I can explain any discount, seat or date-change decision here.")
    status = decision["status"]
    options = {s["strategy_id"]:s["plan"] for s in decision["proposal"]["candidate_strategies"]}
    def title(key):
        if key == "baseline":
            return "Keep the original terms"
        op = options.get(key,{})
        return {"UPDATE_ITINERARY_DETAILS":"Update booking details", "REVISE_ITINERARY":"Apply the requested changes", "SEQUENCE":"Wait for the supplier", "GUARANTEE_AMENDMENT":"Guarantee immediately",
                "UPGRADE":"Offer the room upgrade","ADJUST_PRICE":"Adjust the quote",
                "UPDATE_SEAT_PREFERENCE":"Record the seat preference",
                "ISSUE_TRIP_CREDIT":f"Give {money(op.get('credit_cents',0))} credit"}.get(op.get("op"),key)
    notes = []
    if status in ("APPROVED","COUNTEROFFER"):
        notes.append(f"The verifier selected “{title(decision['strategy'])}”.")
        terms = decision["terms"]
        if terms.get("amendment")=="conditional":
            notes.append("Waiting for a supplier hold avoids promising a date we might have to cancel.")
        if status=="COUNTEROFFER":
            if "credit_cents" in terms:
                requested = decision["proposal"]["goal"]["discount_cents"]
                notes.append(f"The requested {money(requested)} credit fell below the agency’s threshold. "
                             f"The {money(terms['credit_cents'])} credit met it and needs your agreement.")
            else:
                notes.append("The alternative changes the requested terms and needs your agreement.")
    elif status=="REJECTED":
        if decision["proposal"]["goal"]["type"] == "credit":
            previous = decision["snapshot"].get("credit_cents", 0)
            notes.append(f"The proposed credit scored below the agency’s threshold. Credits already applied to this booking total {money(previous)}.")
        else:
            notes.append("Every proposed option scored below the owner's threshold for keeping the original terms.")
    elif status=="CLARIFY":
        notes.append("The request needs an exact date or amount before it can be evaluated.")
    elif decision["reason"] == "Unsupported request":
        notes.append("I couldn’t match that request to a booking change I can check. Could you tell me which part you’d like to change?")
    elif decision["mode"]=="Exact preview":
        notes.append("The model checker was unavailable. The recorded figures are a preview, so the agency issued no offer.")
    else:
        notes.append("The agency could not verify these terms. " + decision["reason"][:180])
    if decision["comparison"]:
        rows = []
        baseline = Fraction(next(r["worst_oracle"] for r in decision["comparison"] if r["strategy"]=="baseline"))
        for r in decision["comparison"]:
            low = r["interval"][0] if r.get("interval") else r["worst_oracle"]
            gain = (Fraction(r["worst_oracle"]) - baseline) / 100
            relative = "reference" if r["strategy"]=="baseline" else f"{float(gain):+,.2f} vs keeping the booking"
            rows.append(f"• {title(r['strategy'])} — {float(Fraction(low))/100:,.2f} ({relative})")
        notes.append("Expected incremental utility in the least favourable scenario (SGD utility units). The original fare is already booked. Negative values measure the modelled cost of handling this request.\n"+"\n".join(rows))
        if decision["proposal"]["goal"]["type"] in ("amend","amend_upgrade"):
            notes.append("Keeping the booking includes assumed lost repeat business. A successful change incurs a supplier fee. An immediate guarantee also exposes the agency to recovery costs and a possible claim.")
    notes.append("Planner: "+decision["source"]+".")
    mode = f" · {decision['mode']}" if decision["models"] else ""
    notes.append((execution["message"]+"\n" if execution else "")+f"Decision {decision['decision_id']}{mode}.")
    return "\n\n".join(notes)


def evidence_text(decision, policy):
    models = decision["models"]
    if not models:
        if decision["reason"] == "Unsupported request":
            return "I couldn’t identify the requested change, so there was no model to evaluate. Tell me what you’d like to change and I can check it."
        return f"Decision {decision['decision_id']} has no evaluated model. The recorded reason was “{decision['reason']}”."
    scenarios = sorted({m["scenario"] for m in models})
    checks = sum(m["runner"].get("status")=="SUCCESS" for m in models)
    lines = [f"Decision {decision['decision_id']} used policy version {policy['version']}.",
             f"The compiler built {len(models)} models across the {', '.join(scenarios)} scenarios. "
             f"PAT returned a successful result for {checks} of them.",
             "Records referenced by these models"]
    if decision["proposal"]["goal"]["type"]=="itinerary" and any("REVISE_ITINERARY" in m["operations"] for m in models):
        lines.append("The revision policy assumes that the change in gross fare passes through to the supplier. The model subtracts that cost from the customer price adjustment and charges the amendment fee only on a successful hold.")
    keys = sorted({key for model in models for key in model["assumptions"]})
    for key in keys:
        rec=policy["records"][key]
        unit,value=rec["units"],rec["value"]
        shown=money(value) if unit=="SGD cents" else (f"{value/100:g}%" if unit=="basis points" else f"{value} {unit}")
        lines.append(f"• {rec['context']} — {shown} ({rec['source']})")
    lines.append(f"Verification query  {models[0]['query']}")
    return "\n".join(lines)
