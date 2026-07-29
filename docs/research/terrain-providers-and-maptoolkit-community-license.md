# Terrain providers and the MapToolkit Community licence

Research for [Set the resilient terrain-provider and licensing boundary](https://github.com/awjreynolds/agentic-satn-compiler/issues/75).

- Retrieved: 2026-07-23
- MapToolkit hosted Terms effective: 2026-07-01
- Research branch: `research/terrain-provider-license`
- Scope: provider and publication decisions, not legal advice

## Conclusion

Keep MapLibre as the renderer, keep the present 2D map as the default and fallback, and add 3D terrain as an optional mode backed by a replaceable `raster-dem` provider.

MapToolkit is not required to add 3D terrain. It may be useful as an optional enhanced vector basemap for the public interactive map, but its Community Edition should not be the foundation of the analytical or resilience contract.

For an initial terrain source, Mapterhorn is a closer fit: its public service exposes terrain across multiple jurisdictions, including England at 1 m, MapLibre's own terrain example uses it, and its implementation is open source. A bounded deployment-area extract can later be self-hosted to remove the hosted-service dependency, subject to the source-data attribution terms.

Google can provide 3D, but it is not the easy drop-in alternative here. It adds keys, billing and a parallel renderer/integration model while still not solving the governed micro-gradient evidence problem.

## Current application boundary

At commit `6ba9228`, the review map:

- renders with MapLibre GL JS 5.6.1;
- uses a raster OpenStreetMap basemap;
- publishes a static interactive `review-map/` directory and ZIP;
- also publishes a separate PDF artifact;
- owns its analytical GeoJSON, layers, selection and details UI.

That makes an in-renderer MapLibre terrain mode materially smaller than introducing Google 3D or Cesium as a second renderer.

## MapLibre capability

MapLibre GL JS natively supports:

- `raster-dem` sources;
- terrain configured against a DEM source;
- pitch and bearing;
- a terrain on/off control;
- a numeric `exaggeration` setting;
- hillshade from a DEM source.

MapLibre's official example currently uses Mapterhorn for both terrain and hillshade.

Source: [MapLibre GL JS — 3D Terrain](https://maplibre.org/maplibre-gl-js/docs/examples/3d-terrain/).

Implication: the mode switch and vertical exaggeration are MapLibre capabilities. They are not MapToolkit features and should not be coupled to a MapToolkit style.

## MapToolkit Community Edition

### Repository licence

The public repository is proprietary, not open source. Its `LICENSE.md` says that:

- publication on GitHub grants only the stated limited rights;
- style JSON may be copied, modified and self-hosted for an application;
- all tile, glyph and sprite references must continue to use official MapToolkit endpoints;
- the styles cannot be retargeted to another provider or independent tile infrastructure;
- the notice must remain with a modified published style;
- the conditional licence terminates automatically on breach.

Source: [maptoolkit/maptoolkit.org — LICENSE.md](https://github.com/maptoolkit/maptoolkit.org/blob/main/LICENSE.md).

This prevents treating the MapToolkit style as a portable styling asset in a provider-fallback strategy.

### README/product boundary

The repository README says:

- no key or sign-up;
- adaptive rate limiting rather than literally unlimited throughput;
- public-facing frontend use;
- no use behind authentication, in intranets or internal tools under Community Edition;
- no SLA or dedicated support;
- vector tiles only; elevation, routing, geocoding, static maps and print maps are Enterprise APIs;
- mandatory MapToolkit and OpenStreetMap attribution.

Source: [maptoolkit/maptoolkit.org](https://github.com/maptoolkit/maptoolkit.org).

### Hosted Terms of Service

The binding hosted Terms effective 2026-07-01 state:

- eligibility requires at least one of non-commercial/governmental non-revenue-generating use, non-commercial OSI-licensed open-source use, or small commercial use under both EUR 1 million consolidated revenue and 10 FTE;
- the licence is non-exclusive, non-transferable, non-sublicensable and revocable;
- use is for interactive frontend maps;
- backend/batch workloads, bulk extraction, offline tile sets and archives are prohibited;
- use in print, exported PDFs, screenshots in offline reports/presentations and other fixed media is prohibited;
- MapToolkit logo at least 24 CSS px plus clickable `© Maptoolkit © OSM` attribution must remain visible;
- the service is best effort, with no SLA, uptime guarantee or backwards-compatibility commitment;
- endpoints may change, deprecate or disappear;
- Community Edition or an endpoint may be discontinued, normally with 90 days' published notice, with shorter/no notice for abuse, legal or security reasons;
- the service must not be used for decisions where errors could cause loss of life, injury, environmental damage or material financial loss;
- terrain/hillshade is currently sourced from `tiles.mapterhorn.com`.

Source: [MapToolkit Community License — Terms of Service](https://www.maptoolkit.org/tos).

### Eligibility and publication assessment

The public MIT-licensed repository and non-revenue-generating governmental review-map use appear to fit the eligibility categories. This is an inference, not provider confirmation.

Two boundaries require written confirmation before adoption:

1. whether the intended council/reviewer deployment is ever authenticated or internal, given the README's explicit restriction;
2. whether distributing an interactive HTML/JavaScript ZIP that requests live service resources is permitted, given the Terms' prohibition on offline tile sets/archives.

The existing PDF must never embed MapToolkit-rendered output under Community Edition. Keep it provider-independent or obtain an appropriate separate licence.

## Mapterhorn

Mapterhorn states:

- public terrain tiles for interactive web maps;
- England coverage at 1 m resolution;
- fully open-source code under BSD-3;
- terrain data comes from multiple open-data sources with source-specific attribution.

The provider's current machine-readable attribution catalogue identifies the England source as
`ukengland`: Environment Agency **LIDAR Composite Digital Terrain Model (DTM) - 1m**, accessed
by Mapterhorn in 2025 and licensed under the Open Government Licence. This is the same national
dataset used by the governed analytical acquisition in this project, but the hosted terrain tiles
remain display-only and are not substituted for the versioned analytical evidence.

Sources:

- [Mapterhorn](https://mapterhorn.com/)
- [Mapterhorn data access](https://mapterhorn.com/data)
- [Mapterhorn attribution](https://mapterhorn.com/attribution)
- [Mapterhorn source attribution catalogue](https://download.mapterhorn.com/attribution.json)
- [Environment Agency LIDAR Composite DTM 1m](https://environment.data.gov.uk/dataset/13787b9a-26a4-4775-8523-806d13af58fc)
- [mapterhorn/mapterhorn](https://github.com/mapterhorn/mapterhorn)

The public host is still an external best-effort dependency. The resilience advantage is that a bounded area can be obtained/packaged and served under project control, rather than making the analytical product depend permanently on an opaque hosted endpoint.

Before self-hosting:

- produce a bounded deployment-area extract rather than mirroring a broad service;
- document update cadence and cache invalidation;
- keep terrain display data separate from governed gradient evidence.

## Google alternatives

Google offers two relevant but distinct paths:

- immersive 3D maps through Maps JavaScript capabilities;
- Photorealistic 3D Tiles through the Map Tiles API and a compatible 3D Tiles renderer.

Photorealistic 3D Tiles require a billing account, API key and visible attribution. Google's official pricing on 2026-07-23 lists:

- Immersive Maps: 5,000 free monthly loads, then USD 7 per 1,000 in the first paid tier;
- Photorealistic 3D Tiles: 1,000 free monthly billable events, then USD 6 per 1,000 in the first paid tier.

Sources:

- [Google Photorealistic 3D Tiles](https://developers.google.com/maps/documentation/tile/3d-tiles)
- [Google Maps Platform pricing](https://developers.google.com/maps/billing-and-pricing/pricing)
- [Google Maps Platform SKU details](https://developers.google.com/maps/billing-and-pricing/sku-details)

Pricing and service terms are volatile and must be rechecked at implementation/procurement time.

Google is feasible, but it is not a like-for-like basemap substitution:

- existing MapLibre layers and interactions would need translation or synchronization;
- a Photorealistic 3D Tiles path normally introduces a 3D Tiles renderer such as Cesium;
- billing/key failure becomes a new degradation mode;
- photorealistic terrain still does not provide the governed, route-aligned 20 m/50 m gradient evidence required by this effort.

## Recommended resilience contract for the decision ticket

1. The current provider-independent 2D analytical map is the default and always remains available.
2. `3D terrain` is a mode on the same MapLibre map, not a replacement application.
3. The terrain source is configured behind a small provider boundary and can be absent.
4. Loading, timeout, attribution or WebGL failure returns the user to 2D without losing the Gradient Inspection Path.
5. Vertical exaggeration is optional, bounded and visibly disclosed; it never changes analytical values.
6. Gradient evidence comes only from the governed topography pipeline.
7. Mapterhorn is the initial terrain candidate; a self-hosted bounded deployment extract is the durability option. No B&NES-specific source is part of the generic renderer contract.
8. MapToolkit is an optional public vector basemap enhancement only. Failure swaps the style/basemap while retaining analytical overlays and selection.
9. The PDF and other static evidence artifacts remain independent of Community Edition output.
10. Google is deferred unless photorealism becomes a requirement that justifies a second renderer, credentials, procurement and ongoing cost.

## Failure cases to prototype

- MapToolkit style, tile, glyph or sprite endpoint unavailable;
- terrain tile endpoint unavailable or slow;
- partial DEM tile failure while a path remains selected;
- WebGL or terrain unsupported;
- switch from 3D to 2D while preserving camera, selection and panel state;
- attribution colliding with the left rail or bottom panel;
- downloadable ZIP opened with and without network access;
- PDF generation in a build that also offers MapToolkit interactively.

## Remaining decisions and unknowns

- Obtain MapToolkit confirmation for authenticated/private review and interactive ZIP distribution.
- Decide whether the public hosted Mapterhorn service is sufficient for the prototype.
- Decide whether self-hosted deployment-area terrain is a production requirement or later hardening.
- Decide the maximum terrain exaggeration and disclosure.
- Define timeouts and the exact user-facing fallback state.
- Recheck Google prices and terms only if Google returns to scope.
