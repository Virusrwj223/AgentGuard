# Northline Travel · AgentGuard

A local travel-booking demo using QwenWork. The traveller plans an itinerary, confirms a demo reservation, negotiates and asks for the explanation within the selected booking's conversation. The website validates cities and looks up fare estimates. A local simulated price keeps demo bookings available when fare sources are unavailable. No airline tickets are issued.

## Free internet search (default)

No account or paid search key is required. The website searches Open-Meteo/GeoNames for real cities, the OurAirports airport-code dataset for airports, Wikipedia for destination photos, and DuckDuckGo for advertised fares. The Singapore–New York and Singapore–Montréal routes also link to published airline service and check airlines’ own fare tables when general search yields no usable price. In **.env**, keep:

```dotenv
AG_TRAVEL_MODE=web
```

“From Singapore, destination Qubec” is accepted as a route description. A fuzzy city match asks you to confirm a suggested spelling, rather than silently changing the destination. Reply with the corrected city name, such as “Québec”.

“Book another trip” starts with empty fields and asks where you are leaving from. A reply such as “Singapore” answers that question. “I am leaving from Singapore, going to New York City” fills both cities, and “two weeks from today I leave” uses the server’s local calendar for departure. Country and region qualifiers are retained: “Destination: Montreal, Canada” selects the Canadian city. For unqualified names, a returned capital city or overwhelmingly larger city can resolve minor namesakes; similarly sized alternatives still require a country. You can reply “Montreal, Quebec, Canada” to clarify.

Advertised fare estimates show the source link, a per-adult SGD price and a group estimate. Published airline service describes routes the airline sells. Check availability for your dates through **Compare current fares**. If there is no usable advertised price, Northline uses a clearly labelled **Simulated demo fare** based on distance, cabin and adult count. You can confirm a demo reservation with either price. The quote is held in the app for 30 minutes. Seats remain unassigned. An internet connection helps with place validation, advertised prices and photos.

Place data: https://open-meteo.com/en/docs/geocoding-api (GeoNames), airport records: https://github.com/datasets/airport-codes (OurAirports). Record sources are retained with each booking.

## Optional Google Flights estimates through SerpApi

SerpApi’s [free plan](https://serpapi.com/pricing) includes 250 searches per month. With a key, the website first requests an SGD round-trip estimate for the selected route, dates and cabin. The API searches for one adult, and Northline multiplies that price by the party size. Airline names from this lookup refer to the outbound leg. If the API cannot find a usable result, Northline continues with the public fare sources and local demo price. See the [Google Flights API parameters](https://serpapi.com/google-flights-api).

To enable it, add your key to **.env** next to **run.py** and restart the launcher. The website server calls the API and QwenWork receives no key. Keep your existing PAT and Qwen settings.

```dotenv
AG_TRAVEL_MODE=web
SERPAPI_API_KEY=your_serpapi_key
```

If an older `.env` selects `AG_TRAVEL_MODE=amadeus`, switch to `web` for this demo. Amadeus closed its [Self-Service portal](https://developers.amadeus.com/api-catalog) in 2026. The amendment model uses synthetic supplier fees and availability, visible in its assumptions.

The validated destination photo appears on the itinerary and in the booking confirmation when a matching photo is found. Its source and licence appear with the image. The New York City demo uses a credited Lower Manhattan photo from Wikimedia Commons. Countries or regions require a specific city or airport; ambiguous search results require a specific choice or IATA code.

## Start on the Windows computer that already runs PAT

1. Right-click the new ZIP in File Explorer and select **Extract All**. Open the inner **agentguard** folder. You should see **start.cmd** and **run.py** together. Keep this folder separate from your previous project.
2. Copy your entire working **C:\codingProjects\patato\pat_lib** folder into this **agentguard** folder. Check that **agentguard\pat_lib\PAT3.Console.exe** exists, with **Modules** and the other supplied files alongside it. Alternatively, copy your working **.env** beside **run.py** if its PAT path already points to the full, working installation.
3. Stop the old demo server if it is using port 8765. Double-click **start.cmd** in the new folder. The first launch creates the Python environment and installs the dependencies. Use Python 3.11 or 3.12 if you are setting up another computer. Leave the launcher window open during the demo.
4. The launcher opens **http://127.0.0.1:8765/**. Look for **PAT console calibrated** in the launcher window to confirm that it can issue negotiated offers. The copied installation is fingerprinted and calibrated automatically on Windows. Its settings are saved beside the demo database. Future starts use the same launcher.

Each server launch clears the demo’s previous bookings, conversations, requests, offers, decisions, audit events and policy drafts from **instance/agentguard.sqlite3** (or the database selected by **AG_DB**). Trips created during a run remain available when you reload the page. Published policy versions, connector credentials and PAT settings carry across restarts. Commands that print connector configuration or keys keep existing demo records.

### If PAT is installed somewhere else

Once the environment exists, open the folder containing **run.py** in File Explorer. Type **powershell** into the address bar and press Enter. These commands register the installation you already use and run the acceptance checks.

```powershell
$p = 'C:\codingProjects\patato\pat_lib\PAT3.Console.exe'
$v = (Get-Item -LiteralPath $p).VersionInfo.FileVersion
.\.venv\Scripts\python.exe run.py --configure-pat $p $v
.\.venv\Scripts\python.exe integrations\check_pat.py
```

The check prints one result per model and records the evidence in **instance/pat-acceptance.json**. Restart the launcher after changing settings. If a previous PowerShell session or copied **.env** still sets **AG_PAT_EXE**, **AG_PAT_VERSION** or **AG_PAT_SHA256**, it takes precedence over the saved installation. Use the settings from your successful calibration. A missing module message calls for the full working PAT distribution. The folder named **.venv** is for Python dependencies.

If you are updating a previous extracted Travel Desk, close its launcher and extract this release into a new folder. Copy your calibrated **pat_lib** and any existing **.env** into that folder. Copy **instance** too if you want to carry over published policy versions, connector credentials and PAT settings. The first launch clears any previous demo activity in the copied database.

## Try a booking and a negotiation

1. Enter “Book a flight from Singapore to Toronto for two adults in economy, 12–16 December 2026, under Raj. Can I get SGD 10 off?” Use future dates. You can also start with a destination and answer one question at a time. Cabin class is required. Names following “under” are traveller names. You can say “window seat”, “aisle seat” or “sit together” to record a preference. Demo reservations leave seats unassigned.
2. The itinerary shows validated locations and an advertised estimate or simulated demo fare. Any discount request is saved until confirmation. Edits to the itinerary invalidate the old quote.
3. Choose **Confirm booking**. The agency writes a reservation with its own **NT-** reference. The negotiation agent picks up the saved request automatically. The current price continues to show what the traveller has booked.
4. For an existing booking, try “Change my cabin to business and depart one day later”, “Change my seat preference to aisle”, a date change, or a credit request. Ask **Explain this decision** or “Show the assumptions”. **Accept offer** applies the exact verified terms. Each additional credit request is evaluated with all previously accepted credits included in its cost. The model may accept, counteroffer or reject; there is no limit on the number of negotiations. Preview mode cannot issue an offer.
5. Try “Move my trip 14 days later”, “Move my trip 14 days earlier” (if the new date is in the future), “Book another trip to Lombok” or “List my trips”. Say “Open NT-…” with a reference from your list to return to a trip. Reloading the same browser restores its conversation. On a small screen, tap **Your itinerary** beneath the chat.

Use the booking selector in the right panel to switch the itinerary and its chat together. The plus button starts another itinerary. The trash button permanently removes that selected demo booking, its requests, decisions, grants, audit events and messages from the active database after confirmation. It does not delete other bookings. Finish an active review before deletion. Legacy multi-booking transcripts cannot be separated reliably and are not shown as context for a selected booking; deleting a legacy booking also clears unassigned legacy messages.

## Use QwenWork with Northline

The website is usable as soon as it opens. Its built-in planner runs locally. To have actual QwenWork handle intake and propose the sales terms from behind the website, connect the worker once per project setup and start a worker conversation for the demo.

1. Keep **start.cmd** running. If QwenWork Desktop is already installed, open it. On a new computer, select **Windows** on the official download page, run the installer, open the app and sign in. [QwenWork download](https://qwenwork.ai/download)
2. Double-click **connect-qwenwork.cmd** in the new **agentguard** folder. It installs the MCP connector dependency, copies the website-worker instructions into your QwenWork skills directory, and places the connector JSON on your clipboard. The JSON contains a private, local planning credential.
3. In QwenWork Desktop, open **Extensions → Connectors → + Add → Paste JSON configuration**. Paste the clipboard with **Ctrl+V**, select **Import**, then enable **northline-web**. It has three tools for health, picking up a request and returning a typed answer. Recent versions also expose custom MCP setup under the Connectors page. [QwenWork connectors](https://docs.qwenwork.ai/desktop/connectors)
4. In a QwenWork conversation, send “Use northline-web-worker to serve the travel desk. Keep listening for website tasks and return each result through the northline-web connector.” Allow the connector’s planning tools if your edition prompts. Keep this session running in the background. [QwenWork skills](https://docs.qwenwork.ai/desktop/skills)
5. Return to the **website**. Straightforward route corrections and quote refreshes are handled there. The worker can serve other intake and negotiation requests through the same connection, including for new bookings. To see which planner submitted a decision, ask “Explain this decision”. A worker submission appears as **QwenWork background worker**. An idle result means the worker is waiting for a website task. If the background conversation stops listening, the website can use an available hosted planner or its built-in planner.

### Run Qwen with Desktop fully closed

If you already have an Alibaba Cloud Model Studio workspace and a key for its region, copy **.env.example** to **.env** beside **run.py** or edit your existing **.env**. Set the following fields using your workspace endpoint and restart **start.cmd**. The server calls Qwen directly. This route uses your Model Studio API access and billing. The explanation labels an accepted proposal from this route **Qwen Model Studio**. [Official compatible API and regional endpoints](https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope)

```dotenv
AG_QWEN_BASE_URL=https://YOUR_WORKSPACE_ID.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1
DASHSCOPE_API_KEY=YOUR_MODEL_STUDIO_KEY
AG_QWEN_MODEL=qwen-plus
```

## What the code runs

| Part | Behaviour |
| --- | --- |
| Browser and chat API | One same-origin chat, contextual prompts and a live itinerary. An HttpOnly cookie scopes travellers to their own records and explanations. |
| Booking intake | Collects origin, destination, future departure and return, party size and lead name. Validates dates and calculates a standard quote. A successful confirmation creates a booking and releases deferred sales requests in the same database transaction. |
| Sales planner | Builds typed candidate plans for booked-trip credits, departures up to 90 days earlier or later, room upgrades, seat-preference edits and supported combinations. A local deterministic planner is always available. The optional Qwen services produce structured inputs. |
| Verification and execution | The compiler and versioned travel policy build complete PCSP models for the baseline and candidate strategies. PAT verifies the models. The service compares the worst scenario against the owner's minimum improvement and applicable floor. A time-limited grant binds the decision to the booking, policy and terms. Only the traveller’s subsequent acceptance can change the simulated booking. |
| Conversation and audit | SQLite stores the conversation, reservation, pending requests, decision and execution events. A browser reconnects to unfinished work. The chat explanation uses the recorded comparison and any execution result. Agency-only APIs from the previous release retain their role restrictions. |

The credit policy evaluates cumulative credits against a synthetic SGD 25 retained contribution per booking. This is an explicit model assumption, not a negotiation-count restriction. Further requests are accepted when their cumulative cost meets the utility criteria. The planner proposes the requested credit and, when applicable, a smaller amount based on remaining modelled contribution; PAT must still verify either proposal. Updates are recorded as a new policy version. The assumptions are in **app/travel_policy.py** and **domain_packs/travel/**.

For bookings more than 90 days before departure, policy AG-03 supplies separately labelled synthetic reference and adverse availability estimates. The figures extrapolate the 8–90 day demo estimates; they are not supplier data. “Show the assumptions” discloses the source and limitation. Existing far-ahead bookings are reclassified when the app starts, preserving prior decision records. Request a fresh decision after updating; an earlier review result cannot become an offer automatically.

When a very large, rare liability makes PAT's single probability too coarse to recover value within five hundredths of a cent, the verifier separately checks each non-dominant terminal outcome probability. It compares those results against the independent exact graph calculation and retains the original run plus probe models in the decision audit. Failed or imprecise probes still require review.

Flights can be revised in a single request covering the route, either travel date, cabin, party size, lead name and preferences. For itinerary revisions that affect inventory, the server obtains a revised gross fare. The policy models the gross fare difference as a matching supplier cost difference. A successful simulated hold also incurs the amendment fee. An unavailable hold preserves the whole original booking. Accepted credits carry forward up to the revised gross fare.

Scores for changes measure incremental agency utility. Under the supplied peak adverse scenario a hold succeeds with probability 0.55. Keeping the original booking costs SGD 150 in assumed lost repeat business. Waiting scores −111.50, an improvement of SGD 38.50. Guaranteeing scores −4,611.50 because the failed-hold branch includes a 1% chance of a synthetic SGD 1 million claim. These figures depend on the selected policy.

Policy AG-05 models a post-confirmation seat-preference edit as a local record update with SGD 0 of supplier or payment costs. PAT compares it with keeping the booked preference. Acceptance applies the exact preference verified by PAT. Flight assignments stay with the airline. A successful date amendment marks the original fare's carrier and seat information stale.

### Code map

| Module | Responsibility |
| --- | --- |
| `app/chat.py` and `app/chat_repository.py` | Route chat commands, persist each turn and project the selected itinerary. |
| `app/booking_intake.py` and `app/trip_intake.py` | Interpret a turn using its pending question, validate the merged draft and format booking replies. |
| `app/language_models.py` | Pass typed planning jobs to QwenWork or hosted Qwen. |
| `app/travel_search.py`, `app/web_travel_search.py` and `app/flight_details.py` | Resolve places, retrieve fares and seat evidence, and attach credited photos. |
| `app/fare_estimates.py` | Read Google Flights prices through SerpApi and calculate local demo fares. |
| `app/sales_desk.py`, `app/compiler.py`, `app/pat.py` and `app/governance.py` | Build typed change proposals, compile and verify them, and apply matching consent. |

The intake refactor follows the CS2103/T textbook's guidance on readable names, cohesive functions and reducing duplicate logic. The chat interprets a turn once, and both travel providers use the same draft validation and fare invalidation flow. [CS2103/T textbook](https://nus-cs2103-ay2627-s1.github.io/website/se-book-adapted/index.html)

## Run the checks

From the directory containing **run.py**, after the Windows launcher has installed dependencies, use PowerShell.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
.\.venv\Scripts\python.exe -m pip install -r integrations\requirements-mcp.txt
.\.venv\Scripts\python.exe integrations\smoke_mcp.py
.\.venv\Scripts\python.exe integrations\check_pat.py
```

The first command exercises intake, persistence, consent, deferred negotiation, authorization and compiler regressions. The second and third exercise both MCP paths using a disposable database. The final command runs **your installed PAT** and includes a seat-preference change through the real verifier and consent flow. The repository includes sample PCSP and a recorded preview in **demo/generated/**. Run **python demo/export.py** to regenerate those examples and the shared proposal schema.

On macOS or Linux, create a virtual environment, install **requirements.txt** and run **python run.py**. The chat and standard bookings work without solver configuration. The PAT check requires a compatible installation and, where applicable, an **AG_PAT_RUNTIME** executable. The demo binds to your own computer's loopback address and does not contact travel suppliers or process payments.

## Design reference

The desktop page gives roughly two-thirds of the width to the conversation. Its blue accents, destination photography and concise booking summary draw on Traveloka Singapore. Northline uses its own wordmark, copy and illustrative cover. [Traveloka Singapore](https://www.traveloka.com/en-sg)
