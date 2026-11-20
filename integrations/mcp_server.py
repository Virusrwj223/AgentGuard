"""Optional role-scoped MCP stdio connector for QwenWork Desktop."""
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from mcp.server.fastmcp import FastMCP
from integrations.handoff import call
from app.contracts import Proposal,Draft,Gap,Summary,Id
server=FastMCP("AgentGuard")
role=os.environ.get("AG_ROLE","sales")
if role not in ("sales","knowledge","risk","audit") or not os.environ.get("AG_ROLE_KEY"):
    raise SystemExit("Set AG_ROLE and its matching scoped AG_ROLE_KEY")
@server.tool()
def health()->dict:
    """Report backend and actual solver availability."""
    return call("/health")
if role=="sales":
    @server.tool()
    def get_booking_context()->dict:
        """Read only this session's request and permitted booking terms."""
        return call("/api/context")
    @server.tool()
    def get_operation_catalog()->dict:
        """Read the supported plan grammar and constraints."""
        return call("/api/catalog")
    @server.tool()
    def evaluate_strategies(proposal:Proposal)->dict:
        """Submit structured proposals to independent governance."""
        return call("/api/evaluate","POST",proposal.model_dump())
    @server.tool()
    def get_authorized_response()->dict:
        """Return backend wording for the current customer request."""
        return call("/api/latest") or {}
if role in ("knowledge","risk"):
    @server.tool()
    def read_knowledge_pack()->dict:
        """Read approved records, contexts and consequence trees."""
        return call("/api/pack")
if role=="knowledge":
    @server.tool()
    def submit_knowledge_draft(record:Draft)->dict:
        """Propose a pending factual change; founder reviews it."""
        return call("/api/drafts","POST",record.model_dump())
if role=="risk":
    @server.tool()
    def submit_risk_review(gap:Gap)->dict:
        """Propose a pending gap for founder review."""
        return call("/api/gaps","POST",gap.model_dump())
if role=="audit":
    @server.tool()
    def get_decision_audit(decision_id:Id)->dict:
        """Read model artifacts, financial terms, and immutable decision events."""
        return call("/api/audits/"+decision_id)
    @server.tool()
    def validate_summary(decision_id:Id,facts:list[Summary])->dict:
        """Check event-linked facts and return the deterministic explanation."""
        return call("/api/audits/"+decision_id+"/summary","POST",[f.model_dump() for f in facts])
if __name__=="__main__":server.run(transport="stdio")
