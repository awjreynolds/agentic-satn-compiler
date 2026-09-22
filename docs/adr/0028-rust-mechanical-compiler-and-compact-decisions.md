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

The implementation and evidence are tracked in
[Build the Rust agentic SATN compiler from the ground up](https://github.com/awjreynolds/agentic-satn-compiler/issues/538).
