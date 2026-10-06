# MVP 20mph limits: casualty prediction

Research resolution for [Establish a supported casualty-reduction method for 20mph limits](https://github.com/awjreynolds/agentic-satn-compiler/issues/639). Checked 6 October 2026. The method below supports a conditional modelled scenario; it is not a locally validated prediction or evidence of achieved benefits.

## Answer

Manchester's paper alone cannot reproduce a KSI casualty forecast. An explicit DfT speed-change scenario combined with outcome-specific Elvik casualty relationships is a possible MVP method, subject to the owner accepting its transfer assumptions and providing actual mean-speed and casualty data.

## Published methods

For **urban 20mph limits without traffic calming**, the [DfT Speed Limit Appraisal Tool guidance](https://assets.publishing.service.gov.uk/media/5a79a26440f0b642860d996e/user-guidance.pdf), Annex A.8–A.10, gives:

`mean_speed_after - mean_speed_before = 4.4038 - 0.2265 * mean_speed_before`

Speeds are in mph. The regression used 347 link observations from seven English authorities, with R² = 0.39 and observed before means of 13.8–32.9mph. That range describes the sample; it is not a universal permitted-speed rule. The guidance supplies a separate traffic-calming relationship and says it has no observations for village 20mph schemes. Applying this urban sign-only equation to rural/village or physical-intervention scenarios needs separate justification. Do not substitute a posted speed limit for measured mean speed, force the forecast mean to 20mph, or silently clamp the regression result. [SLAT guidance, §§2.1–2.3 and Annex A](https://assets.publishing.service.gov.uk/media/5a79a26440f0b642860d996e/user-guidance.pdf).

[Elvik's revised Power Model, TØI 1034/2009](https://www.toi.no/getfile.php/Publikasjoner/T%C3%98I%20rapporter/2009/1034-2009/1034-2009-Sum.pdf), Table S.1, models the ratio of outcomes after/before as the mean-speed ratio raised to an outcome-specific exponent. For **urban/residential casualty counts**, the point estimates are fatal casualties 3.0 and seriously injured people 2.0. Thus, for comparable baseline and forecast exposure:

`r = mean_speed_after / mean_speed_before`

`KSI_after = fatal_casualties_before * r^3 + serious_casualties_before * r^2`

`estimated_KSI_reduction = fatal_casualties_before + serious_casualties_before - KSI_after`

These are casualty coefficients. The report's fatal-accident and serious-injury-accident coefficients are different and must not be used as coefficients for people injured. Fatal-casualty exponent uncertainty is wide (95% interval -0.5 to 6.5); serious-casualty uncertainty is 0.8 to 3.2. Those intervals describe model parameters, not a ready-made joint forecast confidence interval. [Elvik summary and Table S.1](https://www.toi.no/getfile.php/Publikasjoner/T%C3%98I%20rapporter/2009/1034-2009/1034-2009-Sum.pdf).

Combining those equations is a **proposed transferable scenario method**, not a DfT endorsement of an area-wide KSI forecast. The [DfT 2018 signed-limit evaluation](https://www.gov.uk/government/publications/20-mph-speed-limits-on-roads) found median speed reductions under 1mph across its case studies and insufficient evidence to establish a significant residential collision or casualty change. Report scenario assumptions and limitations alongside any estimate.

## Minimum inputs and honest coverage

Use [STATS19 Road Safety Open Data](https://www.gov.uk/government/statistical-data-sets/road-safety-open-data) casualty records joined to collision locations. Collision counts alone cannot tell how many people were killed or seriously injured. Retain baseline years and a stated comparable forecast period; account for injury-severity reporting changes using DfT's provided documentation and adjustments.

SLAT recommends at least three years of accident data for robust link estimates and permits grouping similar links. That is guidance for its accident analysis, not an independently established KSI-casualty sample-size rule. Sparse road-section histories and aggregation must be handled explicitly rather than turning every zero observed count into an assertion of no potential benefit. [SLAT §§3.4–3.10](https://assets.publishing.service.gov.uk/media/5a79a26440f0b642860d996e/user-guidance.pdf).

A minimal implementation can calculate only for newly proposed urban sign-only sections with appropriate measured speeds and attributable casualty evidence, reporting unsupported sections as not estimated. A road already at 20mph gets no new-limit benefit from this scenario. Roads needing physical changes can remain on the proposal layer while their benefit remains unestimated by this model. These are proposed MVP coverage choices for the owner.

## Manchester comparison

The [Manchester paper](https://drive.google.com/file/d/1sdByJVY4nWNBta2bQNVo3nBK34OROqc1/view), pages 12–14, describes a 31-site analysis with typology-specific serious/slight collision relationships. It does not publish those collision coefficients and says it could not establish a robust fatal-collision relationship. Figure 9 publishes a speed-change regression, `-0.6475 * before_mean_speed + 11.552` (R² = 0.3191); that is not a KSI equation. It cannot simply be relabelled as predicted KSI reductions.

## Remaining owner decision

Accept clearly labelled modelled KSI scenarios using the transferable method, or keep the benefit field unestimated until a locally supported method is supplied. Select the mean-speed source, comparable baseline/forecast period and reporting aggregation. Do not invent those inputs to populate the map.
