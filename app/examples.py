"""Scripted proposals are regression fixtures. Live planning is performed in QwenWork."""
def proposal(case,rid,bid):
    def strategy(name,op,**kw):
        return dict(strategy_id=name,plan=dict(op=op,**kw))
    days=14 if bid=="B003" else 30
    goal=dict(type="amend",days=days,max_extra_charge_cents=0)
    conditional=dict(op="SEQUENCE",steps=[dict(op="REQUEST_AMENDMENT",days=days),dict(op="IF",predicate="AMENDMENT_APPROVED",then=dict(op="CONFIRM_AMENDMENT"),otherwise=dict(op="KEEP_BOOKING"))])
    clarify=False
    if case=="amend":
        plans=[strategy("guarantee","GUARANTEE_AMENDMENT",days=days),dict(strategy_id="conditional",plan=conditional)]
    elif case=="discount":
        goal=dict(type="sale",discount_cents=1000)
        plans=[strategy("discount","ADJUST_PRICE",discount_cents=1000)]
    elif case=="upgrade":
        goal=dict(type="upgrade",max_extra_charge_cents=0)
        plans=[strategy("upgrade","UPGRADE",charge_cents=5000)]
    elif case=="compound":
        goal.update(type="amend_upgrade",max_extra_charge_cents=5000)
        conditional["steps"][1]["then"]=dict(op="SEQUENCE",steps=[dict(op="CONFIRM_AMENDMENT"),dict(op="UPGRADE",charge_cents=5000)])
        plans=[dict(strategy_id="conditional_upgrade",plan=conditional)]
    else:
        goal=dict(type="unknown")
        clarify=case=="ambiguous"
        plans=[]
    return dict(request_id=rid,booking_id=bid,goal=goal,needs_clarification=clarify,candidate_strategies=plans)
CASES={
 "amend":"Can I move my booking 30 days later at no extra charge?",
 "discount":"Could I get SGD 10 off this new trip?",
 "upgrade":"Can I get a room upgrade for free?",
 "compound":"Move my trip 30 days later, and upgrade the room if confirmed. I can pay SGD 50 for the upgrade.",
 "ambiguous":"Could we move it to a little later?",
 "unsupported":"Convert my loyalty points into cash."}
