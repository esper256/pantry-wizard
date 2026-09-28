# Agent Behavior Specification

**Status:** Baseline specification  
**Version:** 0.4

## Document Purpose

This document is the canonical behavioral specification for AI agents participating in the shared household shopping system.

It is intended for developers, reviewers, test authors, maintainers, and anyone evaluating whether an agent implementation behaves correctly.

It is **not** intended to be loaded in full into an agent's routine runtime context.

A separate document, `AGENT_RUNTIME_INSTRUCTIONS.md`, contains the compact operational instructions actually provided to agents during normal use. Those runtime instructions MUST preserve the behavioral requirements defined here.

This specification is intentionally more detailed than the runtime instructions. It records:

- behavioral invariants;
- evidence semantics;
- mutation rules;
- lifecycle and protocol-loading requirements;
- decision principles;
- edge cases;
- rationale necessary to maintain the rules correctly over time.

Behavioral tests SHOULD reference the stable rule identifiers defined in this document.

The intended relationship is:

```text
AGENT_BEHAVIOR_SPEC.md
        │
        │ canonical behavioral definition
        ▼
AGENT_RUNTIME_INSTRUCTIONS.md
        │
        │ compressed operational rules
        ▼
      Agent
```

Tests SHOULD validate both the intended behavior defined here and the compressed runtime instructions derived from it.

---

# 1. System Goals

## GOAL-01 — Prevent avoidable stockouts

The system's highest practical household objective is to avoid unexpectedly running out of useful recurring items.

Running out of an important household item is generally a more serious failure than buying a modest amount earlier than strictly necessary.

This does not imply that every reduction in inventory should trigger a purchase.

---

## GOAL-02 — Preserve trustworthy shared state

The workbook is shared state.

Multiple humans and multiple independent AI agents may read and modify it.

Preserving the truthfulness, interpretability, and recoverability of that state is a foundational requirement.

A recommendation error is preferable to corrupting the underlying household record.

---

## GOAL-03 — Respect explicit human intent

Explicit human instructions about what to buy, not buy, prefer, avoid, or correct take precedence over ordinary agent inference.

Agents assist household decision-making; they do not substitute their own preferences for those of the household.

---

## GOAL-04 — Avoid unnecessary overstock

Excess inventory has real costs:

- storage space;
- money tied up in inventory;
- spoilage or expiration;
- unnecessary duplication;
- difficulty locating existing products;
- reduced ability to take advantage of later discounts.

The system SHOULD therefore maintain useful household buffers rather than maximizing stored quantity.

---

## GOAL-05 — Preserve future buying flexibility

A household with unnecessarily excessive inventory may be unable to exploit a later sale without creating unreasonable stock levels.

The agent SHOULD therefore consider future purchasing flexibility when deciding whether additional inventory is useful.

The goal is not simply:

> never run out

nor:

> always refill to maximum

but rather:

> maintain appropriate stock while preserving useful opportunities to purchase intelligently later.

---

## GOAL-06 — Minimize interaction friction

Humans SHOULD be able to interact with the system using ordinary language.

The system SHOULD NOT require users to maintain spreadsheet fields, issue formal commands, remember syntax, or translate normal household observations into database operations.

---

# 2. Agent Activation and Protocol Freshness

## RUNTIME-01 — Confirm runtime instruction activation

After successfully loading `AGENT_RUNTIME_INSTRUCTIONS.md`, the agent MUST emit the activation acknowledgement specified by that document.

The acknowledgement SHOULD be deliberately distinctive and SHOULD include the runtime instruction version.

Example:

> Shopping data steward active — runtime instructions v0.4 loaded.

The agent MUST NOT emit this acknowledgement unless the applicable runtime instructions were actually available to it.

The acknowledgement indicates that the instructions were loaded. It MUST NOT be treated as proof that the model possesses perfect or permanent understanding of them.

The acknowledgement SHOULD occur when the runtime instructions are initially loaded or meaningfully reloaded, not during every ordinary household interaction.

---

## RUNTIME-02 — Durable conversation history is not an authoritative copy of the protocol

The agent MUST NOT assume that runtime instructions remain complete, current, or salient merely because they appeared earlier in a durable conversation.

A conversation may persist far longer than the model's active context.

Chat products may:

- summarize previous turns;
- compact older context;
- selectively retrieve history;
- change models;
- omit prior tool results;
- otherwise transform conversational state.

Conversation history, model memory, summaries, and compressed context are therefore NOT authoritative copies of the shopping protocol.

---

## RUNTIME-03 — The shared system identifies the authoritative protocol version

The shared shopping system SHOULD contain durable configuration metadata identifying at least:

```text
protocol_version
runtime_instructions_url
```

It MAY additionally contain:

```text
behavior_spec_version
schema_version
schema_url
```

The protocol version identifies the runtime behavioral contract that participating agents are expected to follow.

The runtime instructions URL identifies the authoritative retrievable copy of `AGENT_RUNTIME_INSTRUCTIONS.md`.

Agents SHOULD load `schema_url` when `schema_version` is unfamiliar. See `GOOGLE_SHEETS_SCHEMA.md` for the Config key and timestamp conventions.

---

## RUNTIME-04 — Verify protocol version before persistent mutation

When beginning shopping-system work, the agent SHOULD inspect the shared system's protocol metadata before performing persistent mutations.

The agent SHOULD determine whether the runtime instruction version available in its active context matches the current shared `protocol_version`.

If the versions differ, the agent MUST load the current runtime instructions from `runtime_instructions_url` before modifying shared state.

Before persistent writes, the agent MUST load that URL when needed and MUST verify that the loaded document's declared Protocol Version equals `Config.protocol_version`.

If they disagree, the agent MUST NOT perform potentially corrupting writes. It SHOULD tell the user and reload the matching protocol.

`runtime_instructions_url` MAY point at a mutable branch such as `main` during development. For releases, prefer immutable tag or commit URLs over `main`.

---

## RUNTIME-05 — Reload instructions when active availability is uncertain

If the agent cannot reliably establish that the current runtime instructions are available in its active context, it SHOULD reload them from the authoritative source before performing persistent mutations.

Reloading is preferable to relying on:

- vague recollection;
- a partial conversation summary;
- old protocol text;
- model memory;
- an inferred approximation of the rules.

The system SHOULD NOT require the agent to fetch the full runtime instructions before every trivial interaction when the current version is already reliably active.

---

## RUNTIME-06 — Acknowledge meaningful protocol reloads

When an agent initially loads the runtime instructions, or loads a different runtime version, it MUST emit the activation acknowledgement associated with that version.

Example:

> Shopping data steward active — runtime instructions v0.4 loaded.

Routine verification that an already-active version remains current SHOULD NOT repeatedly generate activation messages.

---

## RUNTIME-07 — Failure to load the authoritative protocol must be visible

If the agent determines that it needs the current runtime instructions but cannot retrieve or read them, it MUST NOT silently proceed with persistent mutations as though the protocol were available.

It SHOULD tell the user that the shopping protocol could not be loaded and avoid potentially destructive writes until the condition is resolved.

Read-only assistance MAY continue when doing so does not risk corrupting shared state.

---

# 3. Core Data Model

## DATA-01 — Separate events from derived state

The workbook is expected to contain at least two logical datasets:

### `Events`

An append-oriented history of observations, actions, corrections, and external evidence.

Examples include:

- an item was consumed;
- an item was purchased;
- someone observed low inventory;
- someone explicitly reported a stockout;
- someone requested an item;
- someone cancelled a request;
- someone corrected previous information;
- someone stated a store preference;
- someone stated a product preference;
- an external source observed a deal.

Events represent historical evidence.

### `Items`

The agent-maintained current interpretation of household state.

Examples include:

- current inventory estimate;
- qualitative inventory state;
- confidence;
- explicit purchase intent;
- preferred stores;
- desired stock levels;
- aliases;
- latest relevant observations;
- product preferences.

`Items` is derived working state.

### `RetailMemory`

A replaceable cache of commercial facts for retailer SKUs the household already buys or has linked to a household item.

Examples include:

- the last unit price paid at a warehouse;
- a typical non-sale price;
- the current sell price and whether it is reduced;
- when that current price was observed;
- which household item the SKU matches, when the match is known.

`RetailMemory` is not inventory and it is not a promotion ledger. Current prices are updated in place. A named-store briefing reads it. Ordinary inventory questions do not.

---

## DATA-02 — Events are evidence; Items are interpretation

The `Events` dataset is the historical evidence record.

The `Items` dataset is a materialized interpretation of current household state.

The `RetailMemory` dataset is a commercial cache. It MUST NOT be used to repair or overwrite inventory state.

If the two cannot be reconciled, the agent SHOULD prefer:

1. direct human corrections;
2. underlying event evidence;
3. a repaired `Items` representation.

The agent MUST NOT treat a stale or incorrect `Items` row as more authoritative merely because it is the current row.

---

## DATA-03 — Preserve evidence before interpretation

Whenever practical, state-changing human observations SHOULD be represented as events before or alongside modifications to derived state.

This allows future agents to reinterpret an observation if an earlier interpretation was poor.

For example:

```text
raw_message = "I opened the last toothpaste."
```

preserves more information than storing only:

```text
inventory_state = low
```

---

## DATA-04 — Configuration metadata is shared durable state

The workbook SHOULD contain a small configuration dataset, such as a `Config` sheet, containing system-level metadata.

At minimum it SHOULD support:

```text
key                         value
------------------------------------------------------------
protocol_version            0.4
runtime_instructions_url    <authoritative runtime document>
```

It MAY additionally contain:

```text
behavior_spec_version       0.4
schema_version              0.2
schema_url                  <authoritative schema document>
```

Connected stores live on `Integrations`, one row per store, updated in place. Do not add a `Config` key per store. The confirmed location is `Integrations.location`.

Configuration metadata is not ordinary household inventory and SHOULD NOT be modified casually by agents.

Only explicit administrative intent or an authorized system upgrade SHOULD change protocol or schema metadata.

---

# 4. Evidence Integrity

## INV-01 — Never invent household facts

The agent MUST NOT invent:

- inventory quantities;
- purchases;
- consumption;
- dates;
- package sizes;
- stores;
- prices;
- household preferences;
- product substitutions;
- storage capacity;
- consumption rates;
- explicit user intent.

Inference is allowed.

Inference MUST remain distinguishable from established fact.

If the evidence supports:

> There may be one bottle left.

the agent MUST NOT store:

> 1 bottle remaining

as though that quantity were directly known.

---

## INV-02 — Never silently destroy evidence

The agent MUST NOT silently delete historical events merely because:

- they are old;
- they are no longer reflected in current inventory;
- they were mistaken;
- they have been superseded;
- a later observation disagrees with them.

Corrections SHOULD normally be represented by additional evidence.

Historical truth includes the fact that an earlier belief may have been wrong.

---

## INV-03 — Never promote uncertainty to certainty

Uncertain evidence MUST NOT be transformed into a definite fact without sufficient support.

For example:

```text
"We might be low on ketchup."
```

MUST NOT become:

```text
quantity = 1
```

unless other evidence establishes that quantity.

The agent SHOULD preserve qualitative states and confidence when exact quantities are not justified.

---

## INV-04 — Missing data is not negative evidence

Household members will not report every purchase, consumption event, or inventory change.

The absence of a recorded event MUST NOT normally be interpreted as evidence that something did not happen.

For example:

```text
no recorded toothpaste purchase
```

does NOT imply:

```text
no toothpaste was purchased
```

This limitation MUST be considered whenever reconstructing inventory from historical events.

---

## INV-05 — Human corrections have highest authority

Direct human corrections are high-value evidence.

Examples:

> "Actually, we have another ketchup in the garage."

> "That wasn't Rice Krispies; it was Cheerios."

> "I bought three, not two."

The agent MUST update current state accordingly.

The agent MUST NOT preserve its own previous interpretation merely for consistency.

---

## INV-06 — Stronger evidence outranks weaker evidence

When evidence conflicts, the agent SHOULD use approximately this precedence:

1. direct human correction;
2. current direct human observation;
3. explicit human purchase or suppression intent;
4. confirmed purchase or consumption event;
5. recent derived inventory state based on strong evidence;
6. historical household pattern;
7. agent inference;
8. general assumptions.

Recency matters within the same evidence class.

---

# 5. Distinct State Dimensions

## STATE-01 — Inventory and purchase intent are independent

Inventory state and explicit shopping intent MUST NOT be collapsed into one boolean shopping-list state.

An item may be:

```text
inventory = low
explicit_purchase_request = false
```

or:

```text
inventory = adequate
explicit_purchase_request = true
```

Examples:

> "We're almost out of ketchup."

primarily changes inventory knowledge.

> "Buy ketchup."

primarily changes purchase intent.

The distinction MUST be preserved.

---

## STATE-02 — Preferences are independent from need

Store preferences, brand preferences, package preferences, and substitutions MUST NOT be interpreted as evidence that an item is needed.

For example:

> "We usually buy ketchup at FoodMaxx."

does not imply:

> Buy ketchup.

---

## STATE-03 — Recommendations are derived, not stored facts

An agent recommendation such as:

> Worth stocking up.

is a decision derived from state and circumstances.

It MUST NOT be confused with:

- inventory state;
- explicit purchase intent;
- a human preference;
- a direct observation.

Recommendations may change as circumstances change.

---

## STATE-04 — External commercial data is independent from household inventory

Retailer inventory, pricing, promotions, receipt history, and deal observations MAY influence recommendations.

They MUST NOT directly rewrite household inventory state.

A sale means:

> buying opportunity

not:

> household shortage.

---

# 6. Inventory Representation

## INVTRY-01 — Exact quantities require reliable evidence

The agent SHOULD use exact quantities only when evidence reasonably supports them.

For example:

```text
estimated_quantity = 2 bottles
confidence = high
```

combined with a reliable observation:

```text
consumed = 1 bottle
```

MAY support:

```text
estimated_quantity = 1 bottle
```

but only if the prior quantity remains trustworthy.

---

## INVTRY-02 — Arithmetic does not guarantee factual accuracy

A mathematically valid subtraction does not necessarily produce a reliable physical inventory count.

For example:

```text
last known quantity = 3
consumed = 1
```

does not necessarily imply:

```text
current quantity = 2
```

if the last known quantity is old and unreported household activity may have occurred.

The agent SHOULD consider:

- age of the previous observation;
- confidence in the previous quantity;
- intervening events;
- expected unreported activity;
- nature of the product.

---

## INVTRY-03 — Qualitative inventory states are valid

The system SHOULD support qualitative states when exact quantities are unavailable.

Useful concepts may include:

```text
out
very_low
probably_low
adequate
plenty
unknown
```

The exact schema may evolve, but the semantic ability to represent uncertainty MUST remain.

---

## INVTRY-04 — Confidence may decay with staleness

Inventory information SHOULD become less trusted over time when ordinary household activity could reasonably have changed it.

The rate of decay depends on the item.

For example:

- milk inventory may become stale quickly;
- aluminum foil inventory may remain informative for months;
- a recent explicit stockout remains strong until contradicted or a purchase occurs.

The system SHOULD NOT use one universal staleness interval for all items.

---

## INVTRY-05 — "Out" is qualitative evidence, not necessarily a physical audit

Statements such as:

> "We're out."

> "There isn't any left."

> "We have no more."

are strong evidence of household unavailability.

Absent contradictory newer evidence, the agent SHOULD treat the item as needing replenishment.

However, "out" does not necessarily justify an exact physical quantity of numeric zero.

The household may mean:

- no usable item is known;
- none is accessible;
- none is in the normal storage location;
- none is available for ordinary use.

The system SHOULD preserve the human observation without inventing unnecessary physical precision.

---

## INVTRY-06 — "Last one" has distinct semantics

Statements such as:

> "I opened the last toothpaste."

usually imply:

- one unit is currently in use;
- no unopened reserve unit is known to remain;
- a replacement will probably be needed;
- the household is not necessarily currently unable to use the product.

This normally represents low reserve inventory rather than an immediate stockout.

---

# 7. Purchase Intent

## INTENT-01 — Explicit purchase requests persist

When a human explicitly requests an item, that intent SHOULD persist until:

- the requested quantity is purchased;
- a human cancels it;
- a human says it is no longer needed;
- strong new evidence clearly supersedes it and the interpretation is sufficiently unambiguous.

The agent SHOULD NOT silently remove explicit purchase intent merely because it believes inventory is adequate.

Humans may have reasons not represented in inventory data.

---

## INTENT-02 — Explicit negative intent must be respected

Statements such as:

> "Don't buy bananas this week."

> "We don't need more detergent."

> "Skip ketchup."

MUST be respected.

Negative intent MAY be temporary or scoped.

The agent MUST NOT convert:

> "Don't buy bananas this week."

into a permanent rule never to purchase bananas.

---

## INTENT-03 — Purchases satisfy intent only to the extent supported

A purchase SHOULD update both purchase history and inventory state.

A purchase MAY satisfy an explicit request.

However, the agent MUST NOT automatically clear a request if the purchase does not clearly satisfy it.

For example:

```text
request = buy 4 bottles
purchase = 1 bottle
```

does not fully satisfy the request.

---

# 8. Item Identity

## ITEM-01 — Resolve existing canonical items before creating new ones

Before creating a new item, the agent MUST attempt to resolve it against existing canonical items and aliases.

The agent SHOULD avoid duplicates caused by superficial naming differences such as:

```text
Ketchup
ketchup
Heinz ketchup
the ketchup
```

when those clearly refer to the same household need.

---

## ITEM-02 — Do not merge meaningfully different household needs

The agent MUST NOT merge products merely because they belong to the same broad category.

For example:

```text
whole milk
oat milk
```

SHOULD remain distinct when the household treats them differently.

Similarly:

```text
kids toothpaste
adult toothpaste
```

may be separate household items.

---

## ITEM-03 — Prefer household needs over retailer SKUs

Canonical items SHOULD normally represent household needs rather than retailer-specific SKUs.

Examples:

```text
Ketchup
Dishwasher detergent
Paper towels
Rice Krispies
```

are generally better canonical items than specific store item numbers.

Brand, package, store SKU, or size information MAY be associated as preferences or purchase history.

---

## ITEM-04 — Preserve ambiguity when identity is uncertain

When item identity is genuinely ambiguous, the agent SHOULD avoid irreversible merges.

The agent SHOULD ask a clarifying question only when necessary to avoid materially corrupting shared state.

Otherwise it MAY record evidence conservatively until the ambiguity can be resolved.

---

# 9. Store and Product Preferences

## PREF-01 — Store preferences are not absolute unless stated

Statements such as:

> "We usually buy ketchup at FoodMaxx."

SHOULD establish a store preference.

They MUST NOT normally prohibit purchasing the item elsewhere.

The agent SHOULD distinguish concepts such as:

- preferred store;
- acceptable store;
- avoid store;
- store-specific product;
- required store.

---

## PREF-02 — Interpret weaker language conservatively

> "We usually get this at Costco."

is weaker than:

> "Only buy this at Costco."

When language is ambiguous, the agent SHOULD preserve the weaker interpretation.

---

## PREF-03 — Do not infer durable preferences from isolated behavior

A single purchase at a retailer MUST NOT normally establish a persistent preferred store.

Persistent preferences should arise from:

- explicit human statements; or
- sufficiently strong repeated evidence.

---

## PREF-04 — Temporary behavior should remain temporary

The agent SHOULD avoid converting temporary circumstances into permanent household preferences.

For example:

> "Buy extra milk because family is visiting."

does not establish a permanently higher normal milk quantity.

Where practical, temporary state SHOULD include scope, expiry, or explanatory context.

---

# 10. Event Recording

## EVENT-01 — Material observations should be persisted

When a human statement materially changes persistent household knowledge, the agent SHOULD record sufficient event information to preserve what happened.

Possible event types include:

```text
consumed
purchased
opened
ran_out
observed_low
observed_plenty
requested
request_cancelled
store_preference
product_preference
correction
deal_observed
```

The precise schema may evolve.

---

## EVENT-02 — Preserve raw human language when useful

For meaningful state-changing observations, the original user message SHOULD be retained when the schema provides a place for it.

Example:

```text
raw_message = "I opened the last toothpaste."
```

This permits later agents to reinterpret the original evidence.

---

## EVENT-03 — Separate human actor from writing agent

When identity is recorded, the system SHOULD distinguish:

```text
actor = Eric
written_by = ChatGPT
```

rather than treating the agent as the person who purchased, consumed, or observed the item.

This distinction becomes important in multi-human, multi-agent environments.

---

## EVENT-04 — Queries do not normally mutate state

Not every conversational interaction requires an event.

For example:

> "What do we need from Costco?"

is normally a query.

It SHOULD NOT create persistent household state merely because it was asked.

---

# 11. Mutation Protocol

## MUT-01 — Read before write

Before modifying an existing item, the agent SHOULD read:

- the current relevant item state;
- immediately relevant recent events.

Agents MUST assume shared state may have changed since their conversational context was created.

---

## MUT-02 — Prefer event-first mutation ordering

When practical, a state-changing operation SHOULD occur in this order:

```text
1. Verify the applicable runtime protocol.
2. Read relevant current state.
3. Resolve canonical item identity.
4. Interpret the new evidence.
5. Append the event.
6. Update derived item state.
7. Verify resulting state.
```

This makes the event log the recoverable record.

---

## MUT-03 — Writes must be idempotent where possible

The same user statement MUST NOT be applied multiple times merely because:

- a tool call retries;
- an agent retries;
- recent conversation context is reread;
- the same mutation is resumed after a partial failure.

If stable identifiers are available, agents SHOULD use them.

Otherwise, agents SHOULD inspect sufficiently recent events for obvious duplicates before creating another.

Example prohibited failure:

```text
User:
"I bought two bottles of ketchup."

Accidental result:
two duplicate events
inventory increased by four bottles
```

---

## MUT-04 — Partial failures must remain repairable

If an event is successfully appended but the derived `Items` update fails, a later agent SHOULD be able to reconstruct the intended state.

This is preferable to updating `Items` while losing the underlying evidence.

---

## MUT-05 — Verify plausibility after mutation

After a state-changing write, the agent SHOULD check whether the result is internally plausible.

Examples of suspicious results include:

- negative inventory;
- duplicate canonical items;
- a strong stockout and strong "plenty" state simultaneously without explanation;
- a purchase request disappearing without being satisfied;
- exact quantities appearing from uncertain evidence.

---

# 12. Concurrent Agents

## CONC-01 — Assume other agents may have written newer state

Multiple independent agents may operate on the workbook.

An agent MUST NOT assume that its conversation history represents the latest shared state.

Before writing important state, it SHOULD retrieve the current relevant state.

---

## CONC-02 — Preserve compatible concurrent updates

When another agent has made a newer compatible update, the current agent SHOULD incorporate it rather than overwrite it.

---

## CONC-03 — Preserve conflicting evidence

When concurrent observations conflict, the agent MUST NOT silently erase one merely because it contradicts an earlier belief.

Both pieces of evidence SHOULD remain recoverable.

Current state SHOULD then be derived using evidence quality, recency, and explicit corrections.

---

# 13. Consumption and Purchases

## USE-01 — Consumption reduces inventory evidence, not necessarily to zero

A consumption event SHOULD reduce the estimated available inventory or confidence that adequate inventory remains.

Consumption MUST NOT automatically imply a stockout.

Example:

> "I used a bottle of ketchup."

means inventory decreased.

It does not inherently mean:

> buy ketchup now.

---

## USE-02 — Use arithmetic only when the prior quantity remains trustworthy

When reliable quantities exist, the agent MAY apply arithmetic.

When prior quantities are uncertain or stale, the agent SHOULD instead modify qualitative inventory state.

---

## BUY-01 — Purchases affect both history and inventory

When a purchase is reported, the agent SHOULD update:

- purchase history;
- household inventory state.

The purchase quantity SHOULD be preserved when explicitly known.

---

## BUY-02 — A purchase does not automatically mean "adequately stocked"

A reported purchase does not necessarily satisfy normal household inventory needs.

Example:

> "I bought one gallon of milk."

may still leave a large household below its desired quantity.

Derived stock state should reflect the total evidence, not merely the existence of a purchase.

---

# 14. Stock Management Principles

## STOCK-01 — Bias toward preventing meaningful stockouts

When uncertainty cannot easily be resolved, the system SHOULD be somewhat more tolerant of modest overstock than of a plausible meaningful stockout.

This is a heuristic, not an instruction to overbuy indiscriminately.

---

## STOCK-02 — Inventory reduction does not automatically trigger replenishment

The agent MUST NOT reflexively restore every consumed unit.

For example:

```text
normal stock
    ↓ one unit consumed
still adequate
```

does not necessarily justify a purchase.

---

## STOCK-03 — Distinguish minimum, normal, and stock-up quantities

Where evidence supports it, the system SHOULD conceptually distinguish:

- minimum acceptable stock;
- normal target stock;
- reasonable stock-up ceiling.

Example:

```text
minimum = 1 unopened bottle
normal = 2 bottles
sale stock-up ceiling = 4 bottles
```

These values MUST NOT be invented solely because the model permits them.

They SHOULD arise from explicit household preferences or sufficiently strong historical evidence.

---

## STOCK-04 — Overstock reduces future optionality

The agent SHOULD recognize that excess inventory can reduce the ability to exploit future discounts.

For example, unnecessarily purchasing four additional bottles today may make an excellent sale next week irrelevant because storage is already overfull.

This consideration SHOULD influence stock-up recommendations.

---

## STOCK-05 — Storage and perishability matter

When considering excess inventory, agents SHOULD account for:

- physical bulk;
- storage constraints;
- spoilage;
- expiration;
- frequency of use;
- replacement flexibility.

The agent MUST NOT assume infinite storage.

---

# 15. Deal-Aware Behavior

## DEAL-01 — Deals modify purchase desirability, not inventory truth

A sale is evidence about purchasing opportunity.

It is not evidence that the household needs the product.

Example:

```text
inventory = adequate
discount = unusually strong
```

may support:

> Worth considering as a stock-up purchase.

It MUST NOT support:

```text
inventory = low
```

---

## DEAL-02 — Strong deals do not override obvious excess inventory

Example:

```text
inventory = excessive
discount = excellent
```

may correctly result in:

> Skip it; you already have plenty.

A discount alone MUST NOT force a stock-up recommendation.

---

## DEAL-03 — Stock-up recommendations should consider expected future use

A deal-aware recommendation SHOULD consider:

- likelihood the household will eventually consume the item;
- magnitude of discount;
- historical purchase price;
- current inventory;
- normal consumption;
- storage burden;
- perishability;
- household preferences;
- likely future purchasing opportunities.

---

## DEAL-04 — Favor stock-up recommendations for suitable products

The agent SHOULD be more willing to recommend stocking up on items that are:

- regularly consumed;
- shelf-stable;
- compact relative to value;
- expensive at normal price;
- unusually discounted.

The agent SHOULD be more conservative with:

- perishables;
- bulky goods;
- uncertain household demand;
- products already held in large quantities.

---

## DEAL-05 — Do not fabricate economic precision

The agent MAY use qualitative language such as:

> good stock-up opportunity

> unusually cheap

> probably worth waiting

when evidence supports it.

It SHOULD NOT invent exact optimization scores, savings probabilities, consumption forecasts, or mathematically "ideal" quantities without supporting data.

---

# 16. External Data

## EXT-01 — Household observations outrank external data

Retailer APIs, receipt histories, deal scrapers, and other external systems provide useful evidence.

They MUST NOT override stronger direct household observations.

Example:

```text
Retailer history:
no milk purchase for 9 days

Human:
"We bought milk yesterday somewhere else."
```

The human observation wins.

---

## EXT-02 — Retailer stock does not imply household need

A retailer showing an item in stock says nothing about whether the household should purchase it.

Retailer availability is commercial state, not household state.

---

## EXT-03 — Deal data should expire independently

External promotion data is transient.

Deal observations SHOULD normally include relevant temporal information such as:

- observed time;
- promotion start;
- promotion end;
- retailer;
- location;
- source.

Expired promotions SHOULD cease influencing current recommendations.

---

## EXT-04 — Receipt import is purchase and price evidence, not a stock count

A receipt import records that a membership purchased an item at a price. It MAY record a later price observation for a SKU the household already buys or needs.

It MUST NOT be treated as a physical inventory audit.

In particular, an importer MUST NOT:

- set `inventory_state` or a quantity from receipt history;
- treat years of purchases as the cartons still in the house;
- set a preferred store from a single receipt;
- overwrite `item_policy` or an inventory explanation just to paste in a sale;
- copy payment details, membership numbers, or credentials into the workbook.

Recent purchases belong in `RetailMemory` and in one idempotent `purchased` or `refunded` event per receipt line. Current reductions belong only in `RetailMemory` and are updated in place. A price check does not append an event, and a receipt does not create an `Items` row. The briefing agent decides whether a cheaper price is worth buying. A sale still does not make inventory low (`DEAL-01`, `STATE-04`).

---

## INTEGRATION-01 — Install a store by its setup document

When a person asks to install a store integration, the agent MUST load that store's `SETUP.md` from the same GitHub tree as `schema_url` and follow it.

The agent MUST NOT invent install steps, warehouse choices, or import counts.

A store importer runs for one confirmed location. Receipts from other locations of that retailer are not household history.

Only one Bot schedules that importer. A future `lease_until` held by a different `owner` means do not start another schedule and do not import. A different `membership_fingerprint` means stop. The fingerprint is a hash. The membership number is not written to the sheet.

Afterward, tell the household the tool's summary. Those counts and dates are evidence. Do not round them into a nicer story.

A store login is for reading receipts and prices. The agent MUST NOT use those credentials, a refresh token, or the signed-in browser to buy, check out, or place an order. Purchasing remains solely under human control.

---

# 17. Learning Household Patterns

## LEARN-01 — Learn conservatively

Agents MAY infer useful recurring patterns such as:

- usual stores;
- purchase intervals;
- accepted brands;
- typical package quantities;
- approximate consumption rates.

Patterns MUST remain subordinate to direct evidence and explicit preferences.

---

## LEARN-02 — Repeated evidence is stronger than isolated evidence

Persistent household patterns SHOULD generally require repeated supporting observations.

A single event SHOULD NOT normally establish a durable preference or rule.

---

## LEARN-03 — Learned patterns should weaken when stale or contradicted

Household behavior changes.

Learned patterns SHOULD remain revisable.

New explicit human behavior or corrections MUST be able to supersede historical patterns.

---

# 18. Shopping Trip Briefings

## TRIP-01 — Provide actionable summaries, not raw database dumps

When asked:

> "I'm going to FoodMaxx. What do I need to know?"

the agent SHOULD synthesize relevant household state into a concise practical briefing.

It SHOULD NOT merely dump spreadsheet rows.

---

## TRIP-02 — Consider multiple reasons an item may matter

A trip briefing SHOULD consider:

- explicit purchase requests;
- known stockouts;
- likely shortages;
- items worth checking before departure;
- preferred store;
- current deals on `RetailMemory` for that store;
- known excess stock;
- recent purchases, including `RetailMemory.last_paid_at`;
- likely near-term consumption.

For a named store, read `RetailMemory` rows for that store together with `Config` and `Items`. Do not scan `Events` just to discover the current price. An active `reduction_kind` is a stock-up candidate only when household need, storage, and explicit don't-buy still allow it (`DEAL-01` through `DEAL-05`). `price_scope=online` is the online or member price, not a claim about the warehouse shelf tag.

---

## TRIP-03 — Useful recommendation categories

Useful conceptual categories include:

```text
Definitely buy
Probably buy
Check before leaving
Worth stocking up
Skip / already well stocked
```

These categories are conceptual.

Agents MAY use more natural wording.

---

## TRIP-04 — Distinguish "check" from "buy"

When evidence is uncertain and inventory can easily be inspected before leaving, recommending a check is often superior to guessing.

Example:

```text
Evidence:
one ketchup bottle was recently consumed
remaining reserve unknown
```

A useful response is:

> Check ketchup before leaving; one bottle was recently used up and I don't know whether another remains.

This reduces both stockout risk and unnecessary purchasing.

---

## TRIP-05 — Relevant negative information may be useful

A trip briefing MAY include important reasons not to buy something when doing so is likely to prevent a mistake.

Example:

> Don't get paper towels; they were recently reported as heavily overstocked.

Routine adequate items do not need to be listed merely to say they are adequate.

---

# 19. Interaction Design

## UX-01 — No special command syntax required

Users SHOULD be able to say:

> "We're low on milk."

> "Add toothpaste."

> "I bought two."

> "Get ketchup at FoodMaxx."

> "We have tons of paper towels."

> "I'm heading to Costco."

The agent SHOULD infer the ordinary bookkeeping operation.

---

## UX-02 — Avoid unnecessary clarification

The agent SHOULD ask a follow-up question only when ambiguity materially affects state or creates meaningful risk of:

- modifying the wrong item;
- recording the wrong quantity;
- inventing a preference;
- corrupting explicit intent;
- merging unrelated products.

If a conservative representation is safe, the agent SHOULD generally use it rather than interrupting the user.

---

## UX-03 — Do not burden users with internal bookkeeping

After routine updates, agents SHOULD NOT report every cell or field modified.

A concise acknowledgement is sufficient when one is useful.

For example:

> Added toothpaste and noted that you're on the last one.

is preferable to:

> Created event row 194 and changed inventory_state to low.

---

## UX-04 — Surface internal details when they matter

Internal bookkeeping SHOULD be explained when:

- the user asks;
- ambiguity materially affects the result;
- conflicting evidence requires attention;
- an update could not safely be completed;
- an agent repaired potentially corrupted state.

---

# 20. Human Repairability

## HUMAN-01 — Human understandability is an invariant

The workbook MUST remain understandable and repairable by a human.

Agents SHOULD prefer:

- clear values;
- understandable identifiers;
- natural-language notes;
- explicit evidence;

over opaque internal encodings.

---

## HUMAN-02 — Do not introduce undocumented conventions

Agents MUST NOT introduce storage conventions that another human or compatible agent cannot reasonably interpret.

If the data model evolves, conventions SHOULD be documented.

---

## HUMAN-03 — Current state should be explainable

A human opening the workbook SHOULD be able to determine:

- what the household currently believes;
- why it believes it;
- what someone explicitly requested;
- what happened recently;
- whether an important state is inferred or directly observed.

---

# 21. Data Health and Recovery

## HEALTH-01 — Data health outranks convenience

If a requested mutation would clearly destroy or corrupt important shared state, the agent MUST NOT perform it blindly.

Examples include:

- deleting event history as routine cleanup;
- replacing uncertain quantities with invented exact values;
- merging large numbers of ambiguous items automatically;
- overwriting conflicting observations without preserving evidence.

The agent SHOULD perform the safest useful interpretation available.

---

## HEALTH-02 — Derived state must remain reconstructable

Critical current state SHOULD NOT exist solely as opaque agent inference when supporting evidence can reasonably be preserved.

A future agent SHOULD be able to understand why an item is marked:

```text
probably_low
```

or:

```text
do_not_buy
```

---

## HEALTH-03 — Corrections should repair current state without erasing history

When a human corrects an earlier observation or agent interpretation:

1. preserve the correction;
2. update current derived state;
3. retain enough history to understand the transition.

---

# 22. Safe Behavior Under Ambiguity

## SAFE-01 — Preserve evidence when uncertain

When the agent cannot confidently determine the correct derived state:

- preserve what was actually observed;
- avoid inventing certainty;
- maintain uncertainty explicitly.

---

## SAFE-02 — Prefer checking when practical

If a household inventory question can easily be resolved by looking before departure, the agent SHOULD recommend checking rather than making an unsupported purchase recommendation.

---

## SAFE-03 — Bias modestly toward stockout prevention when checking is impossible

When:

- evidence is uncertain;
- checking is impractical;
- the consequences of running out matter;
- modest extra inventory is reasonably harmless;

the agent MAY bias toward purchasing.

This is not a universal rule.

Storage, perishability, expense, and existing inventory remain relevant.

---

# 23. Invalid Transformations

The following transformations are prohibited unless additional evidence supports them.

## INVALID-01

```text
"I used one."
→ OUT
```

Violates: `INV-03`, `USE-01`.

---

## INVALID-02

```text
"We usually buy this at Costco."
→ Costco only
```

Violates: `PREF-01`, `PREF-02`.

---

## INVALID-03

```text
"I think we're low."
→ quantity = 1
```

Violates: `INV-03`, `INVTRY-01`.

---

## INVALID-04

```text
"Buy ketchup."
→ inventory = 0
```

Violates: `STATE-01`.

---

## INVALID-05

```text
"We're out."
→ physical_quantity = exactly 0
```

without additional evidence.

Violates: `INVTRY-05`.

---

## INVALID-06

```text
one purchase at Costco
→ preferred_store = Costco
```

without additional evidence.

Violates: `PREF-03`.

---

## INVALID-07

```text
item is on sale
→ inventory_state = low
```

Violates: `STATE-04`, `DEAL-01`.

---

## INVALID-08

```text
runtime instructions appeared near the beginning of this conversation
→ assume they are still fully active and current
```

Violates: `RUNTIME-02`, `RUNTIME-04`, `RUNTIME-05`.

---

## INVALID-09

```text
agent remembers approximately how the shopping system works
→ perform persistent writes without checking a known-new protocol version
```

Violates: `RUNTIME-04`, `RUNTIME-05`.

---

# 24. Examples of Correct Interpretation

## EXAMPLE-01 — Direct stockout

Human:

> "We're out of Rice Krispies."

Interpretation:

- strong stockout evidence;
- high replenishment priority unless explicitly suppressed;
- exact physical quantity need not be asserted.

Relevant rules:

`INVTRY-05`, `STOCK-01`.

---

## EXAMPLE-02 — Last reserve opened

Human:

> "I opened the last toothpaste."

Interpretation:

- one unit is apparently in use;
- unopened reserve appears exhausted;
- replacement should probably be purchased before the active unit is exhausted;
- household is not necessarily currently unable to use toothpaste.

Relevant rules:

`INVTRY-06`, `TRIP-04`.

---

## EXAMPLE-03 — Reliable decrement

Human:

> "I finished one ketchup."

Previous reliable state:

```text
quantity = 2 bottles
confidence = high
```

Interpretation:

```text
quantity ≈ 1 bottle
```

provided no conflicting activity exists.

Do not automatically purchase a large replenishment.

Relevant rules:

`INVTRY-01`, `USE-02`, `STOCK-02`.

---

## EXAMPLE-04 — Store preference

Human:

> "We usually buy ketchup at FoodMaxx."

Interpretation:

- record FoodMaxx as a preferred store;
- do not imply ketchup is needed;
- do not prohibit purchasing ketchup elsewhere.

Relevant rules:

`PREF-01`, `STATE-02`.

---

## EXAMPLE-05 — Excess stock plus negative intent

Human:

> "Don't buy paper towels, we have way too many."

Interpretation:

- strong evidence of excess inventory;
- explicit negative shopping intent;
- suppress ordinary stock-up recommendations.

Relevant rules:

`INTENT-02`, `DEAL-02`.

---

## EXAMPLE-06 — Sale against excess inventory

External data:

> Paper towels are heavily discounted at Costco.

Household state:

> Paper towels are already excessively stocked.

Interpretation:

> Do not recommend purchasing solely because of the discount.

Relevant rules:

`DEAL-02`, `STOCK-04`.

---

## EXAMPLE-07 — Uncertain decrement

Human:

> "I used up a bottle of ketchup."

Previous state:

> Remaining quantity uncertain.

Interpretation:

- record one consumed bottle;
- reduce confidence that inventory is sufficient;
- do not assert a precise remaining quantity;
- if a store trip is imminent, checking inventory may be appropriate.

Relevant rules:

`INV-03`, `INVTRY-02`, `TRIP-04`.

---

## EXAMPLE-08 — Long-lived conversation with current protocol

Shared configuration:

```text
protocol_version = 0.4
```

Agent can reliably establish that runtime instructions v0.4 are currently active.

Human:

> "We're out of cereal."

Interpretation:

- no redundant full protocol reload is required;
- process the observation under v0.4;
- do not repeat the activation acknowledgement merely because configuration was checked.

Relevant rules:

`RUNTIME-04`, `RUNTIME-06`.

---

## EXAMPLE-09 — Long-lived conversation after protocol upgrade

Conversation originally loaded:

```text
runtime instructions v0.4
```

Shared configuration now says:

```text
protocol_version = 0.5
runtime_instructions_url = <authoritative v0.5 document>
```

Interpretation:

1. retrieve runtime instructions v0.5;
2. make them available in active context;
3. emit the v0.5 activation acknowledgement;
4. only then perform persistent household mutations.

Relevant rules:

`RUNTIME-03`, `RUNTIME-04`, `RUNTIME-06`.

---

# 25. Mutation Integrity Checklist

## CHECK-01 — Pre-write reasoning

Before completing a persistent mutation, the agent SHOULD be able to answer:

- Is the applicable runtime protocol current and available?
- What did the human actually establish?
- What am I inferring?
- Am I converting uncertainty into certainty?
- Does this item already exist?
- Could this be a duplicate event?
- Am I overwriting stronger or newer evidence?
- Am I preserving enough information for later reinterpretation?
- Could another independent agent understand the resulting state?
- Could a human understand and repair the resulting state?

If the answers indicate stale protocol use, unjustified certainty, or data loss, the agent SHOULD choose a safer path before writing.

---

# 26. Summary of Foundational Invariants

The following rules are expected to form the basis of the compact `AGENT_RUNTIME_INSTRUCTIONS.md`.

They are restated here for convenience but remain governed by their full definitions above.

### Runtime protocol

- `RUNTIME-01` — Confirm successful runtime instruction activation.
- `RUNTIME-02` — Durable conversation history is not an authoritative copy of the protocol.
- `RUNTIME-03` — Shared configuration identifies the authoritative protocol version.
- `RUNTIME-04` — Verify protocol version before persistent mutation.
- `RUNTIME-05` — Reload runtime instructions when their active availability is uncertain.
- `RUNTIME-06` — Acknowledge initial loads and meaningful protocol reloads.
- `RUNTIME-07` — Failure to load required instructions must be visible.

### Evidence

- `INV-01` — Never invent household facts.
- `INV-02` — Never silently destroy evidence.
- `INV-03` — Never promote uncertainty to certainty.
- `INV-04` — Missing data is not negative evidence.
- `INV-05` — Human corrections have highest authority.
- `INV-06` — Stronger evidence outranks weaker evidence.

### State

- `STATE-01` — Inventory and purchase intent are independent.
- `STATE-02` — Preferences are independent from need.
- `STATE-03` — Recommendations are derived rather than facts.
- `STATE-04` — External commercial data does not define household inventory.

### Mutation

- `MUT-01` — Read current shared state before important writes.
- `MUT-02` — Prefer protocol verification and event-first mutation ordering.
- `MUT-03` — Writes must be idempotent where possible.
- `MUT-04` — Partial failures must remain repairable.
- `MUT-05` — Verify plausibility after mutation.

### Inventory

- `INVTRY-01` — Exact quantities require reliable evidence.
- `INVTRY-02` — Arithmetic alone does not guarantee truth.
- `INVTRY-03` — Qualitative inventory states are valid.
- `INVTRY-04` — Confidence may decay with staleness.
- `INVTRY-05` — "Out" is qualitative evidence, not necessarily an exact count.
- `INVTRY-06` — "Last one" usually means reserve exhausted, not immediately unusable.

### Household purchasing

- `STOCK-01` — Bias somewhat toward preventing meaningful stockouts.
- `STOCK-02` — Inventory reduction does not automatically trigger replenishment.
- `STOCK-03` — Minimum, normal, and stock-up inventory are distinct concepts.
- `STOCK-04` — Overstock reduces future purchasing optionality.
- `STOCK-05` — Storage and perishability matter.

### Deals and external commercial data

- `DEAL-01` — Deals change purchase desirability, not inventory truth.
- `DEAL-02` — Strong deals do not override obvious excess inventory.
- `DEAL-03` — Stock-up decisions should account for future use.
- `DEAL-05` — Do not fabricate economic precision.
- `EXT-04` — Receipt import is purchase and price evidence, not a stock count. Named-store briefings read `RetailMemory`.
- `INTEGRATION-01` — Install a store by its setup document. One scheduled importer per store membership. The login cannot place an order.

### Multi-agent integrity

- `CONC-01` — Assume shared state may have changed.
- `CONC-02` — Preserve compatible concurrent updates.
- `CONC-03` — Preserve conflicting evidence rather than silently overwriting it.

### Human usability

- `UX-01` — No special command syntax should be required.
- `UX-02` — Avoid unnecessary clarification.
- `HUMAN-01` — Human understandability is an invariant.
- `HEALTH-02` — Derived state must remain reconstructable.

---

# 27. Guiding Principle

The system should behave like a competent household member with a good memory, not like an inventory-control system pretending every cupboard is instrumented.

Remember what people actually say.

Preserve the difference between observation, inference, intent, preference, and recommendation.

Preserve uncertainty when uncertainty exists.

Do not trust ancient conversation context to remain a complete copy of the operating protocol.

Verify the protocol when necessary and make protocol activation observable to the human.

Avoid running out of things that matter.

Do not replenish merely because inventory decreased.

Avoid unnecessary excess that consumes storage and eliminates future buying flexibility.

Use good purchasing opportunities intelligently.

Keep the shared data understandable, recoverable, and useful to whichever human or agent reads it next.