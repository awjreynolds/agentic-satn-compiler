# Norton–Radstock Greenway provision evidence

Research question: do primary or first-party sources bind the named Norton–Radstock Greenway to existing cycling provision, rather than only to a designation or a proposed improvement?

Retrieval date: **2026-09-20**. The pinned network is `banes-osm-open-roads-v1-2026-07-29`, retrieved **2026-07-23**, with `network.geojson` SHA-256 `6ce1a76491d12f0e57bebba087dc94b89f466dfa91ef109f66c81a34bd5aae59`.

## Finding

Yes. The adopted B&NES Active Travel Masterplan binds the named route to **route-level existing cycling provision**. It describes the Norton–Radstock Greenway as a “heavily used traffic-free cycle route”, gives the Northmead Road–Somervale Road extent, and says it connects to National Cycle Network (NCN) Route 24. This is stronger than a designation or a proposed-link claim.

The follow-up geospatial check now binds **seven retained `ncn-link` records** to the official NCN publisher layer by `SegmentID` and matching geometry. Five are marked `Greenway=Yes`, `TrafficFree`, `Open`, `Asphalt`, with `Quality` `Smooth` or `Standard`; two are adjacent `Greenway=No` link sections, one `TrafficFree` and one `OnRoad`, both reported `Open` and `Asphalt`. The publisher response reports provision and status fields for those seven records. It does not establish the status of every named OSM Greenway feature outside this exact join.

## Primary and first-party sources

| Source | Date and scope | Verified evidence | What it establishes and its limit |
| --- | --- | --- | --- |
| [B&NES Cabinet decision E3594 – Active Travel Masterplan](https://democracy.bathnes.gov.uk/documents/s85699/E3594%20-%20Active%20Travel%20Masterplan.pdf) | Cabinet decision **13 February 2025**; adoption record and final-plan appendices | The decision recommends formal adoption of the Active Travel Masterplan; the Council records the plan as adopted. | The route statement below is an adopted Council plan position, rather than only a consultation proposal. Adoption does not make the plan a live section-by-section condition survey. |
| [B&NES Active Travel Masterplan, final route section](https://www.bathnes.gov.uk/sites/default/files/Active%20Travel%20Masterplan%20-%20Final-part-6.pdf) | Council masterplan; Somer Valley route provision | It calls the Norton–Radstock Greenway a “direct and relatively flat 3.2km traffic-free ride” from Northmead Road in Midsomer Norton to Somervale Road in Radstock, where it meets NCN Route 24. It also describes access to the Five Arches Greenway and NCN Route 24. | Binds the named route to an existing traffic-free cycle connection and named endpoints. It supplies no per-segment identifiers, opening record, or current condition/access values. |
| [B&NES Active Travel Masterplan web page](https://www.bathnes.gov.uk/active-travel-masterplan-closed-consultation) | Council infrastructure inventory page, accessed 2026-09-20 | “Norton Radstock greenway” and “Five Arches greenway” are listed under existing active travel infrastructure; future network categories are listed separately. | Supports the current-versus-planned distinction at network-inventory level. It is not a geometry or maintenance register. |
| [B&NES Placemaking Plan, Somer Valley volume](https://app.bathnes.gov.uk/docs/temp/Planning-Policy/Placemaking-Plan/cs_pmp_vol_4_somer_valley.pdf) | Local-plan route context | The plan describes the Greenway along the Wellow Brook valley and the former railway line between Radstock, Welton and beyond as a traffic-free path. | Corroborates named corridor alignment and provision type. It is older planning policy and gives no current section status. |
| [B&NES Active Travel Network 2024 ArcGIS service](https://services-eu1.arcgis.com/PDjKizfiWWTgpUeI/ArcGIS/rest/services/Active_Travel_Network_2024/FeatureServer) ([NCN Public layer 6](https://services-eu1.arcgis.com/PDjKizfiWWTgpUeI/ArcGIS/rest/services/Active_Travel_Network_2024/FeatureServer/6)) | Official B&NES-named polyline service; layer metadata last edited **2026-02-26 16:52** | The layer exposes the expected geometry and route/status fields, but its local Norton–Radstock envelope query returned zero features. A sample row returned at `[-3.4149, 56.0560]`, outside the pinned study area. | This service does not provide the local binding on the observed response. It is retained here to make the retrieval result explicit rather than silently substituting it for the pinned source. |
| [Walk Wheel Cycle Trust National Cycle Network Public layer 0](https://services5.arcgis.com/1ZHcUS1lwPTg4ms0/arcgis/rest/services/National_Cycle_Network_Public/FeatureServer/0) | Official route-publisher ArcGIS layer recorded by the pinned `snapshot.json`; service metadata reports a last edit of **2026-09-20 00:06:52 UTC**; local envelope query retrieved 14 features | The layer returns Route 24 geometry and fields including `SegmentID`, `GlobalID`, `RouteType`, `RouteNo`, `LinkNo`, `Greenway`, `OpenStatus`, `Surface`, `Quality`, and `Lighting`. Seven rows match the retained context source IDs exactly as `SegmentID`: `18632`, `18633`, `18634`, `36997`, `38642`, `40529`, and `42133`. | The retrieved publisher response supplies the section-level identifiers, matching geometry, and reported fields used below. The seven source IDs have matching point counts and maximum paired-coordinate difference ≤ `1.13e-8` degrees against the retained context geometry. The service-wide metadata edit time is not a per-record observation or field-verification timestamp; the pinned OSM snapshot remains a separate 2026-07-23 retrieval. |
| [B&NES Somer Valley Enterprise Zone project overview](https://www.bathnes.gov.uk/somer-valley-enterprise-zone-project-overview/introduction) | Council future-project page, accessed 2026-09-20 | It proposes a segregated walking/cycling track from Old Mills Lane to integrate with the Norton–Radstock Greenway. | Establishes a future extension/project intent. It must not be counted as existing Greenway provision. |

The pinned snapshot records the route-publisher source URL under `snapshot.json` as `evidence_sources.ncn`. The retained context and planning source inventory already carry the seven source IDs, source hashes, geometry fingerprints, and graph attachments; they do not carry a separate raw NCN export. The raw retained path available for the pinned context is `/Users/awjre/Work/banes-satn/data/snapshots/banes-osm-open-roads-v1-2026-07-29/context.geojson`.

## Official section binding

The retrieved route-publisher response contains these seven exact source-ID matches. `Greenway=No` rows are recorded as adjacent NCN link provision and are not relabelled as Greenway.

| `SegmentID` / retained context `source_id` | FID | `RouteType` / `Greenway` | `Desc_` | `OpenStatus` | `Surface` | `Quality` | `Lighting` |
| ---: | ---: | --- | --- | --- | --- | --- | --- |
| 18632 | 182 | LINK / Yes | TrafficFree | Open | Asphalt | Smooth | NotLit |
| 18633 | 1086 | LINK / Yes | TrafficFree | Open | Asphalt | Smooth | NotLit |
| 18634 | 6968 | LINK / Yes | TrafficFree | Open | Asphalt | Smooth | NotLit |
| 36997 | 23142 | LINK / No | TrafficFree | Open | Asphalt | Standard | FullLit |
| 38642 | 33982 | LINK / Yes | TrafficFree | Open | Asphalt | Standard | NotLit |
| 40529 | 18656 | LINK / No | OnRoad | Open | Asphalt | Standard | FullLit |
| 42133 | 1087 | LINK / Yes | TrafficFree | Open | Asphalt | Standard | NotLit |

The same envelope response also returned Route 24 main-route features outside these seven link records, including `SegmentID` `51657`, `51549`, `2068`, `11268`, `28285`, and `51655`. Their presence confirms the endpoint context but they are not silently folded into the seven retained `ncn-link` matches.

The retained planning source-inventory rows for the seven matches still say `classification: ncn-link` and `provision_status: unknown`, because they were materialised from the pinned context layer rather than from this retrieved publisher response. That retained value is a pipeline provenance limit; it does not override the reported row values above.

## Pinned geometry available for binding

The pinned `network.geojson` contains **16 directed** records named `Norton Radstock Greenway`, all `highway=cycleway` and `oneway=false`, representing **8 unique undirected endpoint pairs**. The six-edge west-to-east chain is **3,226.919952 m**:

| OSM endpoint nodes | OSM way IDs | Length (m) |
| --- | --- | ---: |
| `244308349` → `1640226467` | `151197835`, `168838701`, `407663143` | 785.825388 |
| `1640226467` → `4036518953` | `407663143` | 252.149434 |
| `4036518953` → `4036518975` | `151197834`, `26624188`, `407663143` | 105.040859 |
| `4036518975` → `1640226317` | `26624188` | 1094.544152 |
| `1640226317` → `2310909726` | `26624188`, `261241204` | 699.910299 |
| `2310909726` → `3012275695` | `22932248`, `1361731394`, `261241204` | 289.449820 |

The two additional named short pairs are `3012275694` ↔ `3012275698` (way `1361731394`, 29.222248 m) and `3012275695` ↔ `3012275698` (way `1361731394`, 15.151116 m). Node `244308349` is at `[-2.4925975, 51.2914507]` and joins pinned B3355/Northmead Road records. The main eastern node `3012275695` is at `[-2.4478452, 51.29297]` and joins a pinned Bath New Road/A367 record; a short spur reaches Waterloo Road.

These OSM IDs and coordinates are an exact binding candidate for the pinned graph, not a first-party condition record. The Council names the eastern endpoint Somervale Road; the local OSM endpoint cluster does not carry that name in the retained records. That correspondence remains to be reconciled before treating an official segment match as exact at either endpoint.

## Limits and smallest next action

The adopted plan supports the claim that the named corridor is existing traffic-free provision, and the route-publisher response reports open/surface/quality/lighting values for the seven exact source-ID matches above. It does not establish that every named OSM Greenway feature outside those matches is open today, accessible to every user, or in a given condition. The reported values are a publisher-layer retrieval; its service-wide metadata edit time of **2026-09-20** is not a per-record observation or field-verification timestamp.

The canonical captures are retained under [`build/typesafe-experiments/2026-09-20-greenway-source-evidence/`](../../build/typesafe-experiments/2026-09-20-greenway-source-evidence/):

| Capture | SHA-256 |
| --- | --- |
| `ncn-layer0-metadata.json` | `615afc340fa8e162400783babfeb0f33eaa955ee079d3dc0b16a5521bc012794` |
| `ncn-greenway-envelope.json` | `8b98c0586a5c9f29349c15cf35b26ed4e12445696f89429128b3da69b0249aab` |
| `banes-atn-layer6-metadata.json` | `2d5b3e8bb88b941011bb2d2dd7cfbac730ee30523f3ddb5692b8db4ea5a2c61e` |
| `banes-atn-greenway-envelope.json` | `3faab0ba2e024e5db991f15b9594b8974e24dda4cef4ec1ca889f3cedb911e15` |
| `banes-atn-objectid2.json` | `3837c732ef2272fca7bca85c8781cc84657dbd5827c997dca238543813902089` |

The successful query can be reproduced with this public URL and parameters:

```text
https://services5.arcgis.com/1ZHcUS1lwPTg4ms0/arcgis/rest/services/National_Cycle_Network_Public/FeatureServer/0/query?geometry=-2.493%2C51.291%2C-2.445%2C51.294&geometryType=esriGeometryEnvelope&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=*&returnGeometry=true&outSR=4326&f=json
```

The B&NES comparison used the same envelope and parameters against layer 6 at `https://services-eu1.arcgis.com/PDjKizfiWWTgpUeI/ArcGIS/rest/services/Active_Travel_Network_2024/FeatureServer/6/query`; it returned an empty feature set. Do not infer provision from the OSM `highway=cycleway` tag alone where no official row matches.
