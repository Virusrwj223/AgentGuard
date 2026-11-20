# Validation record

Testing used Python 3.12.14, FastAPI 0.141.1, Pydantic 2.13.4, Uvicorn 0.52.1, HTTPX 0.28.1 and MCP SDK 1.29.0 on Linux. Dependencies are declared in requirements.txt and integrations/requirements-mcp.txt.

- 24 unittest methods passed, including a 16-case labelled plan suite. The cases exercise the supplied proposal, API, compiler and gate. They do not measure natural-language interpretation by QwenWork or performance of real PAT.
- The 16 outcomes matched their labels: four approved plans, one counteroffer, one economically rejected plan, two clarification outcomes, five review outcomes, one invalid schema, one mismatched booking and one stale execution blocked. case-results.json includes the input messages, actual outcomes and per-case integration latency. These timings use the model-reading test double.
- Separate regression tests verified two concurrent consumers produce one successful commit and one rejected replay; a changed terms digest, expired authorization, role mismatch and stale policy also failed.
- Reference fixtures reproduced −115 expected value for the rare-loss example, −2113.50 with a loss multiplier of three, and −901 for a 99%-profitable lottery. The reference amendment case reproduced −150, −2094 and −94 in SGD.
- A genuine MCP stdio client and an actual local HTTP server completed the sales-tool handshake, retrieved a session-bound booking and submitted a typed proposal. The oracle-only backend returned NEEDS_REVIEW. This is connector evidence only.
- Both frontend scripts passed JavaScript syntax checks. The source workspace blocked Chromium's process-socket initialization, so interactive browser acceptance is pending on the demo machine.
- Live PAT execution, signed-in QwenWork use, hosted demo, Hashmeta publication and third-party review of the labelled requests remain unverified. The included video demonstrates the local economic model. Inputs and labels were authored for this implementation and are not an independently held-out evaluation.
