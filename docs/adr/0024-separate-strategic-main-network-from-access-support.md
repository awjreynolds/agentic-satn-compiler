# Separate the Strategic Main Network from Access Support

- Status: accepted
- Date: 2026-08-28
- Supersedes: ADR 0022's initial complete Backbone-and-Access presentation rule

The public SATN is the smallest connected, scope-sensitive Strategic Main Network rather than every route used to serve an Access Obligation. The Effective Strategic Network remains the sole selection authority and retains both main-network and Access Support sections, but its semantic publication projects them separately: existing cycle provision is preferred before A-road corridors, other routable corridors enter the main network only for required continuity or mesh coverage, and Access Support never counts toward mesh conformance. This preserves complete governed access evidence without allowing a dense access tree or a presentation-only visibility change to masquerade as the strategic route mesh.
