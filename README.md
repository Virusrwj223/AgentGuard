# AgentGuard

A local travel-negotiation governance prototype for the QwenWork × Hashmeta AI challenge. A sales workflow proposes strategies. A deterministic compiler calculates their consequences under a versioned synthetic travel pack, emits PCSP, and submits each model to a configured PAT console. A separate executor applies exact authorized terms to a simulated booking.

## Start the project

Use Python 3.11 or 3.12. From the directory containing this README, on Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

On macOS or Linux:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python run.py
```

Open **http://127.0.0.1:8765/**. Select *Change the date at no extra cost* and click **Run sample**. The customer sees **NEEDS_REVIEW** on a machine without a calibrated PAT console. Open **http://127.0.0.1:8765/founder**. Run `python run.py --key founder` with the same environment in a second terminal and paste the private key into the founder console. Select the decision to inspect the scenario comparison, probabilities, PCSP, graph, solver evidence and events. The local narrated walkthrough is **demo/AgentGuard-walkthrough.mp4**.

The default database is `instance/agentguard.sqlite3`. Set `AG_DB` before launch to use a different local database. Each customer session gets its own copy of a synthetic booking. **Open booking** starts a fresh session. The server binds to loopback. Private financial data and approval credentials belong on the agency machine.

## What has been exercised

| Component | Delivery and evidence |
|---|---|
| Compiler, scenario comparison, SQLite authority, customer and founder app | Implemented. Numerical, API and enforcement tests pass. See demo/validation.md. |
| QwenWork integration | Four installable role Skills, typed MCP stdio tools and a JSON transfer client. An actual MCP client performed a local handshake, read a booking and submitted a proposal. A signed-in QwenWork session still needs to demonstrate these steps. |
| PAT PCSP | Deterministic generated models, subprocess adapter, pinning, two startup calibration jobs, precision checks and a real-console acceptance command. The supplied environment had no PAT binary. No real PAT result is bundled. |
| Hashmeta AI CMS | CMS page copy, page structure, publication instructions and a reference-page builder. No published site URL is claimed. |
| Walkthrough | Three-minute narrated walkthrough of actual exact-graph output. It can be used for rehearsal. The competition recording still needs live platform footage. |

## Travel example

All values are incremental SGD with an identity utility profile. The common starting point is an existing reservation. The lower figure for each plan across the two scenarios determines its robust score.

| Strategy | Reference (80% hold) | Adverse (70% hold) |
|---|---:|---:|
| Decline change and keep booking | −150 | −150 |
| Guarantee immediately | −2,094 | −3,101 |
| Request binding hold, then confirm or keep | −94 | −101 |

Conditional service improves on rejection by SGD 56 in the reference case and SGD 49 across the enumerated scenarios. Immediate failure incurs recovery; the synthetic claim occurs on 1% of those failures. The combined reference claim probability is 0.2%. Each incremental value is counted once. No tier of expected utility overrides a missing permission, an unsupported scenario or a stale authorization.

Other samples cover a new-sale discount (SGD 120 list, SGD 90 cost, SGD 10 discount), an upgrade requiring consent, a compound amend-and-upgrade plan, ambiguity and loyalty conversion. B003 uses a peak context and a mock unavailable response. Each booking has specific dated inventory, a simulated business clock and a supplier version. The fixed mock supplier response is a reproducible observation for execution; the scenario probabilities describe uncertainty before the response. A new date requires refreshed inventory for subsequent amendments.

## Use PAT on a supported host

Install a compatible PAT 3.x distribution with PCSP and its dependencies. Keep the console, module hierarchy and DLLs installed together. The application runs PAT on the same machine as the backend. Windows support in the project requires the `pywin32` dependency. An absolute `AG_PAT_RUNTIME` may be supplied on a compatible host with a tested managed runtime. No proprietary console or module is redistributed.

Example PowerShell, after replacing the file path with your installation:

```powershell
$env:AG_PAT_EXE = (Resolve-Path 'C:\PAT\PAT3.Console.exe').Path
$env:AG_PAT_SHA256 = (Get-FileHash $env:AG_PAT_EXE -Algorithm SHA256).Hash.ToLowerInvariant()
$env:AG_PAT_VERSION = (Get-Item $env:AG_PAT_EXE).VersionInfo.FileVersion
.\.venv\Scripts\python.exe integrations/check_pat.py
.\.venv\Scripts\python.exe run.py
```

If the distribution has empty file-version metadata, set the operator-confirmed distribution version explicitly. Save the version and fingerprint from the validated installation. The candidate CLI invocation is `PAT3.Console.exe -pcsp -engine 0 -behavior 0 model.pcsp result.txt`. Confirm it with your distribution. The runner will accept a single assertion-specific minimum-probability verdict only. Missing output, syntax differences, low precision, an incompatible runtime or a failed job blocks approval. The runner uses a private directory, two concurrent slots, a per-process memory cap and an eight-second wall timeout. On Windows, incompatible job-object restrictions cause a tool error. Preserve the raw result when adapting to a newly observed PAT output format.

The acceptance command checks two startup probabilities, four arithmetic fixtures and all six flagship models on the actual binary. It emits `instance/pat-acceptance.json` after calibration, including stdout, result text, version, model and cross-checks. Verify all checks before recording a PAT demo. Five printed decimal places may be insufficient for the rare-loss model. Where the installed console supports it, configure greater precision and rerun acceptance. No tolerance is relaxed to obtain authorization.

### Query, bounds and decision rule

Every production model contains exactly:

```c
#assert Business() reaches economicSuccess with pmin;
```

For terminal utility X, lower bound L and upper bound H, the synthetic terminal success probability is (X−L)/(H−L). The decoder returns L+(H−L)p. Equal bounds use a deterministic success branch. The compiler uses the actual reachable bounds of each individual model to limit rounding amplification. Different models' probabilities cannot be compared directly. Decoded utilities share units. The graph fixes agent choices and enumerates finite stochastic branches only.

The independent exact-rational dynamic program evaluates business edges directly. A separate forward traversal supplies path probabilities, monetary values and utility contributions. The adapter compares PAT to the exact probability and decoded utility for this graph. It permits at most 0.00001 probability discrepancy and 0.05 utility-cent discrepancy; its conservative comparison uses the interval enclosing both decoded and exact scores. This is an a posteriori comparison against a known reference for the graph. It does not certify arbitrary PAT convergence. Missing or uncertain jobs prevent authorization across the entire decision.

For each strategy take the worst scenario endpoint. Require the candidate's lower endpoint to clear the baseline upper endpoint plus the owner's minimum gain and optional floor. Prefer satisfying the request to asking for separate consent, then rank within that class. Incomparable numerical intervals abstain. Exact ties within a class use the lexicographically first strategy ID. The loss multiplier acts on terminal losses before expectation.

## Connect genuine QwenWork workflows

1. Install `integrations/requirements-mcp.txt` in the same Python environment. Keep the backend running.
2. Install the four folders under `agents/skills/` through QwenWork Desktop's Skills screen or place them under the account's `~/.qwenwork/skills/` directory. Use separate role sessions.
3. In the customer desk enter the real negotiation text and choose **Prepare QwenWork handoff**. This creates a session-bound request. Copy the sales credential from the private handoff into the connector's environment. Keep the customer channel and founder key away from the agent.
4. Configure a custom stdio MCP service with the absolute Python executable and the absolute path to `integrations/mcp_server.py`. The example configuration is in `integrations/mcp.example.json`. Set `AG_ROLE=sales`, `AG_ROLE_KEY` and `AG_URL` in the connector host. Reconfigure the sales key when starting a fresh synthetic booking. Give this role only its sales connector.
5. Invoke **agentguard-sales** in QwenWork. Ask it to retrieve the booking, interpret the customer message, propose alternatives and call `evaluate_strategies`. Verify the real tool call and request ID. Use **Check response** in the customer desk. Only the desk can consent and call the mock executor. The QwenWork chat is an internal drafting interface.
6. Configure **knowledge**, **risk** and **audit** connectors with matching `AG_ROLE` and the corresponding secret returned by `python run.py --key knowledge`, `--key risk` or `--key audit`. The connector refuses founder/customer roles. Use `demo/supplier-note.txt` for knowledge onboarding. Review pending facts in the founder console. The risk role submits gaps; the audit role checks event-linked exact fields and falls back to the deterministic record.
7. Record the actual QwenWork session, connector inventory, submitted plan and matching backend decision ID for the submission. Redact every credential.

**Manual QwenWork transfer.** If your edition cannot connect stdio, give the actual QwenWork session the request, booking context, and `shared/proposal.schema.json`. Have it export a JSON plan file, then use `python integrations/handoff.py evaluate plan.json` from the backend environment. The CLI prompts for a scoped key or reads `AG_ROLE_KEY`. Use the same helper with `context`, `catalog`, `draft record.json`, `audit DECISION_ID` or `response`. Explicitly label this as a manual transfer. The sample replay is independent of QwenWork.

Platform references: [QwenWork Skills](https://docs.qwenwork.ai/desktop/skills) · [QwenWork connectors](https://docs.qwenwork.ai/desktop/connectors) · [PAT PCSP manual](https://pat.comp.nus.edu.sg/resources/OnlineHelp/htm/scr/3.3%20PCSP%20Module/3.3%20Probability%20CSP%20Module.htm) · [Hashmeta AI CMS](https://hashmeta.ai/platform/ai-cms).

## Knowledge, authorization and audit

The travel pack contains approved numerical records, bounded contexts, exclusions, templates and an explicit conditional event graph. The pack interpreter accepts registered numeric references, typed operation arguments, additive effects and conditional branches. A cycle, missing required dependency, unapproved assumption, incomplete family coverage or invalid integer fails closed. New operator semantics and new contextual dimensions require engineering review. The founder can edit existing parameter drafts, dismiss or publish them, update the utility profile, and suspend an operation by approving a material gap. Publications produce immutable pack versions in SQLite. The deterministic evidence view preserves the pack used by each historical decision.

A server-stored five-minute grant binds the session, decision, current request, exact terms, model hashes, booking/supplier versions and active knowledge/profile versions. The customer explicitly accepts the displayed terms digest. The transaction validates the grant, reads the simulated supplier observation, performs a bounded state update, consumes the grant and appends execution evidence atomically. A conditional amendment that gets no hold keeps the original date. The demo models prepaid/settled transactions and bounded 90-day contribution. It performs no external communications, card charging or supplier updates.

The journal records structured events and a digest of the request text. The session stores the original request for planning. Local SQLite access is the trust boundary. Application-level append-only logging provides traceability. For private review, see `/docs` for endpoint schemas and `/api/audits/{decision_id}` for the manifest, event list, solver traces and XAI paths. Public/customer responses omit supplier margins and loss assumptions.

## Test and reproduce

```sh
python -m unittest discover -v
python demo/export.py
python -m pip install -r integrations/requirements-mcp.txt
python integrations/smoke_mcp.py
```

The first command checks numerical reduction, correlated losses, conditional plans, review/publish, privileges, stale/altered tokens, concurrent replay, solver failures and labelled cases. Some tests deliberately inject independent test-only solvers. The application exposes no solver substitution endpoint. The POSIX subprocess fixture is skipped on Windows; run the real Windows console acceptance command there. The MCP smoke command uses a disposable backend and a real SDK client. `demo/export.py` regenerates the flagship preview audit and the six model files.

## Directory map

| Location | Responsibility |
|---|---|
| app/contracts.py, shared/ | Strict plan grammar, validation and exported schemas |
| app/compiler.py, domain_packs/travel/ | Plan interpretation, closure, exact paths and PCSP |
| app/pat.py, app/limited_run.py | Isolated solver, calibration, decoding and precision |
| app/governance.py, app/store.py | Scoring, atomic execution, snapshots and audit |
| app/api.py, frontend/ | Customer desk, founder console, access-scoped HTTP API |
| agents/skills/, integrations/ | QwenWork skills, scoped MCP, JSON handoff and acceptance checks |
| demo/, tests/ | Labelled requests, generated models, audit, walkthrough, regression evidence |
| hashmeta/ | Public CMS copy, reference layout and publication steps |

## Submission handoff

Follow `hashmeta/CMS.md` to create and publish the public site inside Hashmeta AI CMS. The optional reference-page generator accepts the team's actual HTTPS demo and contact destinations. Build and publish in the CMS, test its public CTA and record the URL. Capture the real PAT and QwenWork runs and replace the corresponding portions of the local walkthrough before submitting it as platform evidence. All financial and supplier assumptions are synthetic. Formal results apply to the finite compiled models and specified conditions.
