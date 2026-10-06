# MVP 20mph limits: classification and escalation

Research resolution for [Establish road classification and escalation rules for 20mph limits](https://github.com/awjreynolds/agentic-satn-compiler/issues/638). Checked 6 October 2026. This records evidence and a proposed MVP method, not an adopted speed-limit policy.

## Answer

The available primary sources support road-section screening followed by explicit review. They do not supply a complete automatic mapping into Manchester's four recommendation categories. Assess area-wide 20mph **limits** across the road network; traffic-calmed zones have distinct requirements and are not the requested output.

## Supported rules

England's [DfT Setting local speed limits guidance](https://www.gov.uk/government/publications/setting-local-speed-limits/setting-local-speed-limits), updated 17 March 2024, identifies collision history, road geometry and function, road users, existing speeds and road environment as relevant factors. It recommends targeted, road-by-road consideration with a safety case and local support; the guidance is not binding and decisions belong to the traffic authority. These factors can inform the proposed layer without claiming it makes a statutory decision.

DfT paragraphs 100–105 say an existing **mean speed at or below 24mph** is likely to support general compliance with a signed-only limit. For multi-road schemes it says to consider implementation where those means are already achieved across a number of roads. This is a suitability indicator, not a statutory cutoff. DfT does not require traffic calming whenever the mean exceeds 24mph. The Manchester paper's description of supporting measures as required above that value overstates the English guidance. [DfT guidance](https://www.gov.uk/government/publications/setting-local-speed-limits/setting-local-speed-limits).

Welsh guidance treats 30mph as an exception to Wales's 20mph default. It says 30mph is inappropriate within a 100m walk of listed facilities or where residential/retail frontage exceeds 20 premises/km. These are **Welsh reference criteria**, not English requirements. Adopting them for this layer would be a declared owner policy choice. [Welsh Government guidance](https://www.gov.wales/setting-30mph-speed-limits-restricted-roads-guidance-highway-authorities-html).

The [Manchester paper](https://drive.google.com/file/d/1sdByJVY4nWNBta2bQNVo3nBK34OROqc1/view), pages 8–10 and 16, uses movement/place typologies, traffic volumes, frontage, nearby destinations, observed speeds and walking/cycling infrastructure. Its refinement outcomes are: retain existing limit; 20mph with design interventions; sign-only with monitoring; sign-only. The supplied paper does not publish the cutoffs or full rules assigning those outcomes.

## Smallest proposed MVP

1. Retain existing and proposed limits separately for each source-identified road section. Cover the configured plan area's road network, not only selected SATN corridors.
2. Show the available speed, hierarchy, environment/frontage, destination and collision evidence with its source. Missing evidence remains unknown.
3. Use the DfT 24mph indicator to support a sign-only candidate. Higher measured speeds or conflicting/insufficient evidence flag review; they do not automatically cause a higher-limit exception or a physical-intervention requirement.
4. Record the reviewed recommendation and its reason using the four discussed outcomes. A proposed exception, monitoring outcome or physical treatment needs an explicit reason. Keep unresolved cases visible.

This workflow is an inference from the sources, not a published Manchester algorithm. It avoids inventing a movement/place score or unspecified thresholds. Publication and checking a proposal are separate from traffic orders and implementation.

## Remaining owner decision

Accept this conservative screening/review workflow, or explicitly adopt additional sourced criteria that automate more of the classification. The owner must choose what evidence supports an exception, monitoring or physical intervention; the sources do not make that policy choice for them. Implementation can use supplied/reviewed outcomes while absent inputs remain visible, rather than requiring a new survey or legal-process platform for the MVP.
