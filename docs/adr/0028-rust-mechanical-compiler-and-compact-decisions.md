# Rust mechanical compiler with compact decisions

The owner chose a ground-up Rust implementation on 2026-09-22 after the Python
planner spent substantial time copying, hashing, validating and persisting whole
planning states between mechanical decisions. Build native source admission,
in-memory mechanical planning and publication, retaining the Python implementation
and experiments as reference; do not port that bookkeeping into Rust.

Keep compact typed decisions, attribution, uncertainty and model receipts for
replay and branching. Validate at input, model-response and output boundaries;
full-state cryptographic attestation per decision is outside this POC. Preserve
stable source inputs and all A-road corridors, including visible justified
departures. Demonstrate B&NES before the final WECA publication.

The front end admits the pinned sources into an indexed graph and corridor
inventory. The mid-end applies mechanical rules first, asking Jev only about a
focused semantic choice between admitted alternatives. Unresolved judgments may
go to the configured reasoning model; code validates the resulting operation.
The backend publishes geometry, decisions and remaining uncertainty together.
An unknown source fact alone is not a reason to invoke a model, and a model
cannot convert that unknown into an observed fact. Corridor inventory records
are not automatically individual inference tasks.

Keep a prepared planning base once, followed by ordered decision records with
local IDs. Record the focused model task and result once, and refer to them from
the applied operation. Replay applies those recorded operations without model
calls. Branching retains the chosen decision prefix and starts a new continuation;
later decisions from the old branch are not silently applied to the new one.
Previously accepted decisions and unresolved facts are available to subsequent
tasks. This needs local references and typed operations, not per-step hashes of
the entire network or versioned caller input shapes.

For the British POC, preserve the existing NCN evidence rule in
`evidence.mark_ncn_edges`: a 20 m metric corridor buffer and at least 50% edge
overlap. Use pure-Rust geometry and an explicit gridless WGS84-to-British-National-
Grid Helmert transformation, checked against the currently available reference
transformation. The local reference has no OSTN15 grid; this choice does not claim
OSTN15 accuracy. Preserve current, reclassified and greenway evidence separately.
Existing route-role search weights generate alternatives; they do not establish
current provision or become model-confidence acceptance thresholds.

The implementation and evidence are tracked in
[Build the Rust agentic SATN compiler from the ground up](https://github.com/awjreynolds/agentic-satn-compiler/issues/538).

Rust does not require wire compatibility with the experimental Python history.
For a focused semantic choice, an explicit unresolved classifier outcome can
escalate the same task to the configured reasoning model. A transport or malformed
response remains a failed classifier attempt, even if reasoning subsequently
resolves the task. Neither model can supply missing observations. Keep an
unresolved decision when the available evidence cannot support a selection.
The owner's provisional-choice permission is explicit run policy: a supported
best guess needs a reason and retained uncertainty. Provision remains unknown
unless independently sourced. The old routing preference cutoffs do not become
selection or confidence gates in this implementation.

A strategic POC also accounts for current/former cycle-network sources and
existing cycleways, plus governed community access obligations. The owner's
2026-09-22 scope clarification makes schools informational map points rather
than strategic access obligations; school-route planning is a separate scope.
Town-pair choices are only one part of that network. Missing access or attachment
is a visible gap, and an unconfigured destination profile remains explicit.
Do not claim complete access coverage from successful urban route generation.

The rural access extension uses admitted villages and hamlets as access targets,
with the existing city/town set as onward destinations. Urban neighbourhoods stay
contextual in this scope. Find the nearest reachable, graph-bound strategic spine
by measured route length. A separate entry is useful when its complete graph
journey to a named urban destination is shorter than going through the primary
entry; retain that comparison and deduplicate shared access paths. Do not impose
two connections, cardinal directions, guessed travel speeds or new route weights.
The onward graph journey may leave the spine, so it is not a claim of continuous
spine-only provision. A community reference-point attachment is explicitly inferred
and retains its offset; no synthetic segment or observed entrance is invented.
Unreachable access stays a gap, and current provision remains unknown.
