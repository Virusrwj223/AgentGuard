# Release checks

The 35 automated tests ran on Python 3.12 with a disposable SQLite database for each test. The Windows acceptance command and QwenWork setup are in the project README.

| Check | What it establishes |
| --- | --- |
| Application and compiler regression suite | Covers the original 16 labelled plans, expected-utility calculations, roles, counteroffers, uncertainty, stale terms and simultaneous attempts to consume one grant. The model-reading test double exercises the full approval and execution path. |
| Website reservation and handoff | Covers a previously unknown destination, conversational intake, a persisted quote, confirmation before any sales activity, fixed and percentage discount requests, and preserving the booked price until consent. Additional cases exercise two trips, both dates moving together, expiry, restart and strict separation between customer sessions. |
| Optional planners | A test worker receives and submits a real leased planning job over the API. A mock hosted Qwen service exercises request construction, validation, fallback and planner attribution. |
| MCP interoperability | The MCP SDK opens the stdio subprocess and a real local HTTP server. It uses the original sales connector and the new background worker connector. A website submission reaches the worker, the independent engine returns a preview, and the browser session retrieves the recorded planner attribution. |
| Frontend | The client script passes the JavaScript syntax check. A browserless JavaScript harness loaded the real client over HTTP and exercised message submission, quote rendering, contextual hints, background polling, acceptance, refresh and the mobile itinerary disclosure. |

The suite uses synthetic prices and local supplier outcomes. **integrations/check_pat.py** runs the actual acceptance models on the configured PAT executable. The Windows PAT installation, a signed-in QwenWork session and a live Model Studio account require a check on the demo computer. The live visual browser preview was unavailable in the build environment.

For a constant-utility model, the compiler now emits a 50% terminal probe. PAT must report a numeric probability close to 0.5 to verify that model. Startup calibration exercises this same probe. PAT 3.5.1 previously reported only “VALID” for the deterministic version of this model, which caused six credit models to go to review. Qualitative verdicts and incorrect numbers still cannot authorize a negotiated offer. The new encoding and the acceptance check must be exercised with the operator's actual PAT installation.

The flagship amendment example in **generated/** records the exact graph preview for the baseline, immediate guarantee and conditional supplier hold. **case-results.json** contains outcomes and timings for the legacy fixture suite. Neither captures live PAT timing.

The original recording and transcript are retained in **demo/legacy/** as a record of the previous interface. The demonstration for this release is the chat sequence in the README.
