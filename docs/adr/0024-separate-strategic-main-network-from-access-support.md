# Separate the Strategic Main Network from Access Support

- Status: accepted
- Date: 2026-08-28
- Supersedes: ADR 0022's initial complete Backbone-and-Access presentation rule
- Amended by: ADR 0026's A-road backbone as the primary selection objective; the main/access split and stable presentation remain applicable

The public SATN is the smallest connected, coverage-preserving, scope-sensitive Strategic Main Network rather than every route used to serve an Access Obligation. The compiler joins reachable candidate components through the minimum bidirectionally routable Strategic Main Connectors required for continuity; unreachable candidate islands remain explicit Network Gaps rather than becoming parallel Main networks. The Effective Strategic Network remains the sole selection authority and retains both main-network and Access Support sections, but its semantic publication projects them separately: existing cycle provision is preferred before A-road corridors, other routable corridors enter the main network only for required continuity or mesh coverage, and Access Support never counts toward mesh conformance. This preserves complete governed access evidence without allowing a dense access tree or a presentation-only visibility change to masquerade as the strategic route mesh.

On 2026-09-05 the owner clarified that the POC must keep this network visually
stable while making the map understandable to officers and laypeople. The default
view shows the Strategic Main Network with a consistent structural style and named
places. Asset inventories and analytical styling remain opt-in; grouped controls
and a plain-language feature preview keep technical detail available without
making it the first thing a reader sees. Controls that share rendered layers
reconcile their combined visibility requirements. Hover, pinning, and context
toggles must not change the main network's source, filter, or structural style.
