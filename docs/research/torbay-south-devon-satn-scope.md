# Torbay and South Devon SATN deployment scope

## Decision

The `torbay-south-devon` Area Deployment compiles one continuous analytical
network across the **current full authority boundaries** of:

| Authority | Current ONS code | OSM boundary query |
| --- | --- | --- |
| Torbay | `E06000027` | `Torbay, England, United Kingdom` |
| Teignbridge | `E07000045` | `Teignbridge, Devon, England, United Kingdom` |
| South Hams | `E07000044` | `South Hams, Devon, England, United Kingdom` |

The three boundaries are evidence and context inside one network compilation.
They do not split the network or imply that every route is the responsibility
of the authority in which it is drawn.

## Why this geography

Torbay is a compact unitary authority centred on Torquay, Paignton and Brixham.
Its immediate strategic connections continue across the current boundary into
Teignbridge and South Hams. Torbay Council's own planning material identifies
both councils as neighbouring authorities, and its housing evidence describes
Torbay's functional area as extending into parts of both. Using their complete
current districts gives the POC a stable, reproducible boundary definition
rather than inventing an ungoverned partial-council catchment.

This is a **cross-boundary analytical proof of concept**, not an adopted plan,
not a proposal for council reorganisation, and not a claim about future highway
or planning responsibilities. It uses the compiler's clean generated baseline:
there is no officer decision ledger, named officer scenario, route override or
ATM geometry in the Area Definition.

## Boundary currency

The authority names and codes were checked against the Office for National
Statistics' current local-area pages:

- [Torbay — E06000027](https://www.ons.gov.uk/explore-local-statistics/areas/E06000027-torbay)
- [Teignbridge — E07000045](https://www.ons.gov.uk/explore-local-statistics/areas/E07000045-teignbridge)
- [South Hams — E07000044](https://www.ons.gov.uk/explore-local-statistics/areas/E07000044-south-hams)

Torbay Council announced on 16 July 2026 that a future reorganised Torbay
council is expected to include 21 surrounding parishes currently in
Teignbridge and South Hams. That announcement does not itself replace today's
legal authority boundaries. This deployment therefore uses the three complete
current authorities and must be regenerated from a revised governed Area
Definition when new legal boundaries take effect.

Sources:

- [Torbay Council: Local Government Reorganisation decision announced, 16 July 2026](https://www.torbay.gov.uk/news/pr9538/)
- [Torbay Council: current boundary and neighbouring-authority planning context](https://www.torbay.gov.uk/council/policies/planning-policies/helaa/)
- [Torbay Council: Torbay housing sub-market evidence](https://www.torbay.gov.uk/council/policies/planning-policies/hena/hena-3/)

## Reproducible build

The governed definition is
[`deployments/torbay-south-devon/area.yaml`](../../deployments/torbay-south-devon/area.yaml).
It uses the existing OSM/Overpass and Walk Wheel Cycle Trust acquisition seams,
the standard bike-network compiler settings, a non-production fake direct
runtime for the experimental POC, and no officer overrides.

The immutable snapshot, compiled output, public deployment and Pages bundle are
generated artifacts under ignored `data/` and `build/` directories. Their
content hashes are recorded in the deployment provenance lock and release
evidence; the spatial artifacts are not committed to Git.
