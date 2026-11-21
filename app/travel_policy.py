"""Add the published post-booking credit policy without replacing existing approved records."""
from copy import deepcopy


def with_credit_policy(pack):
    if pack.get("chat_policy_version", 0) >= 1:
        return pack
    updated = deepcopy(pack)
    records = updated["records"]
    records["credit_retention"] = dict(
        records["retention"], id="credit_retention", value=2500,
        context="90-day contribution lost when declining a post-booking goodwill request",
        source="Northline synthetic goodwill policy AG-02",
        evidence="Demo assumption: declining a goodwill request costs SGD 25 of future contribution.",
        limitations="Synthetic fixed amount per request; one credit per booking."
    )
    keep = updated["operations"]["KEEP_BOOKING"]
    keep["tree"]["events"].append("credit_declined")
    updated["required_events"]["KEEP_BOOKING"].append("credit_declined")
    updated["events"]["credit_declined"] = {
        "when": {"seeking_credit": True},
        "tree": {"label": "goodwill request declined", "set": {}, "delta": ["-credit_retention"], "events": []}
    }
    updated["operations"]["ISSUE_TRIP_CREDIT"] = {
        "version": 1, "pre": {"done": False, "credited": False},
        "tree": {"label": "credit against existing booking", "set": {"done": True, "credited": True},
                 "delta": ["-$credit_cents"], "events": []},
        "coverage": dict(updated["operations"]["ADJUST_PRICE"]["coverage"], retention="covered")
    }
    updated["chat_policy_version"] = 1
    return updated


def with_advance_policy(pack):
    """Publish separately identified demo assumptions for departures over 90 days away."""
    if pack.get("advance_policy_version", 0) >= 1:
        return pack
    updated = deepcopy(pack)
    for name, original in (("advance_available", "normal_available"),
                           ("advance_adverse", "normal_adverse")):
        rec = deepcopy(updated["records"][original])
        rec.update(
            id=name,
            context="advance booking, 91 or more days before departure"
                    + (", adverse scenario" if name.endswith("adverse") else ", reference scenario"),
            source="Northline synthetic advance booking policy AG-03",
            evidence="Demo assumption: extrapolated the fixture's 8–90 day availability estimate to advance bookings.",
            limitations="Synthetic extrapolation without supplier observations; availability may differ for departures over 90 days away.",
            version=1,
        )
        updated["records"][name] = rec
    updated["scenarios"]["advance"] = {"reference": "advance_available", "adverse": "advance_adverse"}
    updated["advance_policy_version"] = 1
    return updated


def with_cumulative_credits(pack):
    if pack.get("cumulative_credit_version") == 1:
        return pack
    updated = deepcopy(pack)
    updated["operations"]["ISSUE_TRIP_CREDIT"]["tree"]["delta"] = ["-$credit_cents", "-$prior_credit_cents"]
    updated["operations"]["ISSUE_TRIP_CREDIT"]["version"] += 1
    updated["records"]["credit_retention"]["limitations"] = "Synthetic retained contribution per booking; compare total accepted and proposed credits against this contribution. No limit on negotiation count."
    updated["records"]["credit_retention"]["source"] = "Northline synthetic cumulative concession policy AG-04"
    updated["records"]["credit_retention"]["evidence"] = "Demo assumption: retaining this booking contributes SGD 25; all accepted and proposed credits count against that same contribution."
    updated["records"]["credit_retention"]["version"] += 1
    updated["cumulative_credit_version"] = 1
    return updated


def with_seat_preference_policy(pack):
    """Publish the cost model for a local preference update without supplier actions."""
    if pack.get("seat_preference_policy_version") == 1:
        return pack
    updated = deepcopy(pack)
    updated["records"]["seat_preference_cost"] = dict(
        updated["records"]["credit_retention"],
        id="seat_preference_cost",
        value=0,
        approved_range=[0, 0],
        source="Northline synthetic seat preference policy AG-05",
        context="Local seat preference edit on a confirmed demo reservation",
        evidence="The update only changes a preference in the local reservation record.",
        limitations="No supplier request, assigned seat, fare change or availability guarantee.",
        evidence_status="synthetic",
        version=1,
    )
    updated["operations"]["UPDATE_SEAT_PREFERENCE"] = {
        "version": 1,
        "pre": {"done": False},
        "tree": {
            "label": "save local seat preference",
            "set": {"done": True, "seat_preference_updated": True},
            "delta": ["-seat_preference_cost"],
            "events": [],
        },
        "coverage": {
            "cost": "covered",
            "availability": "N/A: no supplier inventory action",
            "payment": "N/A: no charge",
            "fulfilment": "covered",
            "liability": "N/A: no seat assignment or guarantee",
            "retention": "N/A: no retention assumption for this operation",
            "opportunity": "N/A: no supplier or capacity commitment",
        },
    }
    updated["required_events"]["UPDATE_SEAT_PREFERENCE"] = []
    updated["seat_preference_policy_version"] = 1
    return updated


def with_itinerary_policy(pack):
    if pack.get("itinerary_policy_version") == 1:
        return pack
    updated = deepcopy(pack)
    # In this policy the change in supplier cost equals the change in the gross fare.
    # Customer credits survive a revision up to the gross value of the new booking.
    coverage = dict(updated["operations"]["CONFIRM_AMENDMENT"]["coverage"],
                    availability="covered", payment="covered", retention="covered")
    held = {"label": "revised itinerary held at repriced fare", "set": {"done": True, "amended": True},
            "delta": ["+$customer_delta", "-$supplier_delta", "-fee"], "events": []}
    failed = {"label": "revision unavailable", "set": {"done": True, "hold": "failed"},
              "delta": [], "events": ["rejection"]}
    updated["operations"]["REVISE_ITINERARY"] = {
        "version": 1, "pre": {"done": False}, "coverage": coverage,
        "tree": {"probability": "@revision_availability", "yes": held, "no": failed},
    }
    updated["required_events"]["REVISE_ITINERARY"] = ["rejection"]
    local = deepcopy(updated["operations"]["UPDATE_SEAT_PREFERENCE"])
    local["tree"].update(label="save local booking details", set={"done":True,"details_updated":True})
    local["tree"]["delta"] += ["+$customer_delta", "-$prior_credit_cost"]
    local["coverage"].update(payment="covered",retention="covered")
    updated["operations"]["UPDATE_ITINERARY_DETAILS"] = local
    updated["required_events"]["UPDATE_ITINERARY_DETAILS"] = []
    updated["itinerary_policy_version"] = 1
    return updated
