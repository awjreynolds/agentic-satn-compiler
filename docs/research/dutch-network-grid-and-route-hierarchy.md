# Dutch cycle-network grid and route-hierarchy principles

Research for [Verify Dutch network-grid and route-hierarchy principles](https://github.com/awjreynolds/agentic-satn-compiler/issues/257).

- Retrieved: 2026-07-30
- Research branch: `research/dutch-network-grid`
- Scope: primary Dutch national, CROW and municipal evidence; implications for
  configurable parallel-section comparison in the UK compiler
- Status: research note; **not an accepted ADR or a Dutch-compliance claim**

## Conclusion

No primary source reviewed establishes a Dutch cycling hierarchy with mandatory
connectivity at 100 m, 500 m, 1 km and 2 km. That four-number shorthand conflates
different modes, purposes and contexts:

- CROW's cycle-design guidance describes **300–500 m** as the usual mesh width of
  an urban main cycle network and **1,000–1,500 m** as common practice outside
  built-up areas.
- CROW's pedestrian-design guidance uses **70–120 m** (approximately 100 m) for
  the urban **walking** base network and a maximum of **1 km** for green,
  relaxed **walking** routes.
- The same pedestrian page lists 500 m, 1 km, 2 km and 4 km only in a table
  illustrating the extra travel time caused by different mesh widths. The 2 km
  row is not a recommended cycle-network layer.
- Separate CROW barrier guidance, described in an official Dutch parliamentary
  report, recommends maximum distances between barrier crossings of 500 m
  inside, 1,000 m at the edge of, and 1,500 m outside built-up areas. These are
  barrier-crossing criteria, not a hierarchy of cycle routes or a rule for
  deleting parallel alignments.

The defensible lesson for this compiler is therefore **hierarchical,
destination-connected and context-sensitive network planning**, not adoption of
the four-number shorthand.

A configurable 1 km distance can legitimately be used as a broad
**parallel-section candidate-discovery radius**, especially when searching
across mixed urban/rural areas. It must not be described as a Dutch rule or
treated as sufficient evidence of substitution. Selection still needs evidence
that the sections serve the same strategic movement, connect at usable
divergence and rejoining points, and remain coherent with destinations,
barriers, directness, safety and comfort. A nearby route with a distinct access
or resilience role may be complementary rather than redundant.

## What national Dutch policy establishes

The 2022 **Nationaal Toekomstbeeld Fiets** (National Future Vision for Cycling)
was produced through Tour de Force with national, provincial, transport-region
and municipal participation. It presents a shared direction, while stating that
the participating public authorities retain their own responsibilities and
powers. It also says the vision has no direct effect and must be translated into
the relevant policy plans
([NTF, pp. 6 and 34](https://open.overheid.nl/repository/ronl-c7e81e32154ccb96643ac477e562518c37d24dea/1/pdf/nationaal-toekomstbeeld-fiets.pdf)).

The NTF supports the following network principles:

- Routes within, between and around urban and rural areas should form a
  fine-grained network whose backbone is a national network of high-quality
  cycle routes (p. 20).
- A functioning network provides short, attractive and findable routes, safe
  connections and sufficient capacity to all important destinations, including
  public-transport nodes (p. 26).
- Main routes form the basis. They serve large numbers of cyclists or medium-
  to long-distance journeys. Underlying routes through neighbourhoods and the
  countryside, using separated facilities or safe mixed-traffic roads, combine
  with the main routes to make the fine-grained network (p. 26).
- Main-route roles differ: regional routes connect settlements and large
  residential areas to employment, facilities and secondary schools;
  intra-urban routes connect districts and daily destinations; recreational
  main routes connect settlements with recreational destinations (p. 26).
- Strengthening can mean improving an existing main route, upgrading an
  existing cycle route to main-route quality, adding a missing link, or removing
  a barrier (pp. 26–27).

The NTF does **not** prescribe a national mesh width or a distance at which a
parallel route becomes redundant. It describes functions and outcomes and
leaves spatial implementation to the responsible authorities.

## What CROW cycle guidance establishes

CROW is the Dutch knowledge platform whose design guidance is routinely used by
Dutch road authorities. Its **Ontwerpwijzer fietsverkeer** (Design Manual for
Bicycle Traffic) provides the clearest technical account of mesh width and
hierarchy.

### Mesh width is a network-coherence indicator

CROW defines mesh width as the distance between more or less parallel
connections. Network coherence depends on both mesh width and the degree of
interconnection: a smaller mesh with poor or excessive junctions is not
automatically better. The manual describes mesh width as a simplified indicator
of main-network coherence and states:

- within built-up areas, the usual main-cycle-network mesh is **300–500 m**;
- outside built-up areas, serving the important connections between regional
  centres, schools, employment areas and public-transport access points often
  produces a mesh of **1,000–1,500 m** in practice.

The same page says a good main network uses a limited set of links and junctions
to carry a large share of cycle kilometres; around 70% of cycle kilometres on
the main network is an indication that it matches movement needs
([CROW, “Samenhang”](https://kennisbank.crow.nl/public/gastgebruiker/WOBU/Ontwerpwijzer_fietsverkeer/Samenhang/32929)).

These are guidance ranges and an evaluative indicator. They are not a guarantee
that every place should have parallel routes at those exact intervals, nor a
rule that the closest existing route replaces a proposed one.

### The hierarchy is functional

CROW distinguishes three cycle-network levels:

1. **basisstructuur** — the base structure;
2. **hoofdfietsnetwerk** — the main cycle network; and
3. **snelle fietsroute** — a fast/high-quality regional cycle route.

The correct provision for a link depends on its place in that cycle hierarchy as
well as the motor-traffic road category
([CROW, “Functie, vorm en gebruik”](https://kennisbank.crow.nl/public/gastgebruiker/WOBU/Ontwerpwijzer_fietsverkeer/Functie%2C_vorm_en_gebruik/32952);
[CROW, “Uitgangspunten”](https://kennisbank.crow.nl/public/gastgebruiker/WOBU/Ontwerpwijzer_fietsverkeer/Uitgangspunten/32962)).

A cycle main route need not follow a motor-traffic main road. CROW explicitly
notes that separating them can improve safety, attractiveness and delay, and
that a main cycle route can instead use a purpose-designed route or local
access roads configured as a bicycle street
([CROW, “Fietsstraten”](https://kennisbank.crow.nl/public/gastgebruiker/FVV/Ontwerpwijzer_fietsverkeer/Fietsstraten/32964)).

Fast cycle routes are described as the highest-level backbone of the regional
network. Importantly for parallel-route reduction, CROW also allows a
**ladder structure** in a corridor: multiple fast or supplementary routes can
offer different recreational or experiential qualities. Its ambitious criteria
include route choice and a lower-traffic alternative
([CROW, “Hoofdeisen aan een snelle fietsroute”](https://kennisbank.crow.nl/public/gastgebruiker/WOBU/Ontwerpwijzer_fietsverkeer/Hoofdeisen_aan_een_snelle_fietsroute/32942)).

Parallel geometry therefore does not establish duplication. Two routes can be
complementary when they provide meaningfully different access, operating
conditions, experience, resilience or user choice.

### Route quality is multidimensional

CROW's five high-level requirements are coherence, directness, attractiveness,
safety and comfort. Coherence includes complete door-to-door connectivity,
wayfinding, consistency, route choice and handling barriers. Comfort includes
avoiding unnecessary effort from elevation as well as vibration and delay
([CROW, “Hoofdeisen fietsvriendelijke infrastructuur”](https://kennisbank.crow.nl/public/gastgebruiker/FVV/Ontwerpwijzer_fietsverkeer/Hoofdeisen_fietsvriendelijke_infrastructuur/32349)).

This supports presenting population, topography, access, directness and
existing-infrastructure evidence separately. It does not support replacing
those dimensions with distance-to-nearest-route or one composite “Dutch grid”
score.

## What municipal practice demonstrates

Municipal plans show that Dutch authorities adapt the hierarchy and mesh to
place rather than apply one national numerical ladder.

### Amsterdam

Amsterdam's adopted Plusnet and Hoofdnet cycle networks together have an
approximate **400 m** grid width. The Plusnet functions as the city's
through-cycle network
([Amsterdam, “Plusnet en Hoofdnet Fiets”](https://maps.amsterdam.nl/fietsnetten/)).

The city's network framework assigns different priorities rather than treating
one level as the only usable network:

- Plus networks provide flow quality for the largest streams and receive the
  highest priority when networks compete;
- Main networks retain enough space to carry traffic.

([Amsterdam, “Plusnetten en hoofdnetten infrastructuur”](https://maps.amsterdam.nl/plushoofdnetten/)).

This is evidence for a declared strategic/local role and different quality or
priority expectations. It is not evidence that nearby Main-network links should
be removed when a Plusnet route exists.

### Zaanstad Hembrug

The adopted Hembrug mobility plan states that a fine-grained structure based on
CROW guidance uses approximately **50–100 m for pedestrians** and
**300–500 m for cyclists**. It links that local-development grid to route choice,
distribution of movement, direct connections and good links to surrounding
destinations
([Zaanstad, Mobiliteitsplan Hembrug, section 4.1.2](https://lokaleregelgeving.overheid.nl/CVDR692361/1)).

This is a useful official example of mode-specific mesh widths. It also shows
why the 100 m number must not be presented as a cycling-network requirement.

### Uithoorn

Uithoorn's transport plan uses approximately **250 m** for its underlying cycle
network and supplements motor-traffic corridors with cycle-only shortcuts so
people can reach a main route quickly. It also directs routes toward schools,
centres, stations and major public-transport stops
([Uithoorn, Verkeer- en Vervoerplan, section 7.1](https://lokaleregelgeving.overheid.nl/CVDR306099)).

The different local value reinforces that a mesh width is a plan choice tied to
urban form and destinations, not a universal national constant.

## Where the 100 m / 500 m / 1 km / 2 km shorthand goes wrong

CROW's **pedestrian** design manual says:

- 70–120 m is a good mesh for the walking base network;
- no mesh guideline is given for the walking main network because it consists
  of specific routes connecting important origins and destinations;
- the green, relaxed walking network should have a maximum 1 km mesh; and
- a table estimates extra journey time at mesh widths of 500 m, 1 km, 2 km and
  4 km for several modes.

([CROW, “Ontwerpwijzer voetgangers — Directheid”](https://kennisbank.crow.nl/public/gastgebruiker/WOBU/Ontwerpwijzer_voetgangers/Directheid/118112)).

The 2 km entry is an input to a detour-time illustration—about ten minutes for a
cyclist at the table's assumed speed—not a recommended cycle-network mesh. The
page itself says the approximately 100 m ideal is for the urban walking base
network.

A separate official parliamentary report on barriers states that CROW
recommends maximum spacing between places where people can cross a barrier of:

- 500 m within a built-up area;
- 1,000 m at its edge; and
- 1,500 m outside it.

It assesses barrier effect together with detour factor and crossing difficulty
([Dutch House of Representatives, Kamerstuk 33 888, no. 2, paragraphs 178–188](https://zoek.officielebekendmakingen.nl/kst-33888-2.html)).

Those figures can inform a future **barrier and crossing evidence profile**. They
must not be silently repurposed as parallel-route substitution radii.

## Safe implications for the compiler

### 1. Keep the proximity threshold configurable and accurately named

Use a versioned `parallel_candidate_proximity` (or equivalent), with units,
spatial method, context and sensitivity recorded. An initial 1 km value is a
product hypothesis for broad discovery, not “the Dutch standard”.

Consider allowing an urban/rural profile:

- the CROW 300–500 m urban main-network range can be a diagnostic sensitivity;
- 1,000–1,500 m can be a rural diagnostic sensitivity.

Those ranges should not automatically change the selected network. A UK
Guidance Profile or explicit council configuration must own the operative
threshold.

### 2. Do not equate mesh width with substitution

Mesh width describes a property of a connected network. Parallel candidate
proximity is a search operation. A section is a plausible substitute only when
the compiler can demonstrate at least:

- equivalent strategic role and direction of movement;
- usable topological connection at the divergence and rejoining boundaries;
- continuity through the whole compared section;
- destination and Access Obligation implications;
- directness and detour evidence;
- barrier and crossing implications; and
- independently visible safety, comfort, topography and existing-facility
  evidence.

Straight-line separation alone cannot satisfy those tests.

### 3. Preserve complementary routes

Classify close sections as `substitute`, `complementary` or `unresolved` before
selecting one. Retain both as network roles when they serve distinct
destinations, provide materially different access, form a useful ladder,
provide a necessary low-traffic alternative, or cannot connect at credible
decision points.

The selected Preferred Strategic Alignment may combine sections from different
source corridors. Rejected substitute sections should remain inspectable, but a
complementary route must not be greyed out as though it lost the same decision.

### 4. Treat hierarchy as role, not source-road class

A-roads, the National Cycle Network and local streets are evidence sources and
physical contexts; they do not by themselves determine the cycle-network role.
A strategic section can legitimately leave an A-road and use a suitably
configured local street before rejoining. The compiler should assign
strategic/main/access roles from connection purpose and governed evidence, then
assess the facility or intervention needed for that context.

### 5. Use destinations and population without misquoting Dutch evidence

Dutch sources repeatedly connect main routes to settlements, schools,
employment, public transport and other important destinations. That supports
the compiler's explicit Access Obligations and Population Reach evidence.

It does not establish a Dutch population-capture radius, a population threshold
for choosing between alternatives, or a benefit–cost rule. Those remain
versioned UK compiler policy choices and should retain their own sensitivity and
provenance.

### 6. Keep deterministic decisions inspectable

The evidence supports a bounded decision packet rather than an automatic
distance rule. The packet can expose:

- why the sections were identified as parallel;
- their functional role and join points;
- population and destination evidence;
- directness, barriers, topography, safety and comfort evidence;
- existing-infrastructure status;
- whether the alternatives are substitute or complementary; and
- the selected action, deterministic fallback and rejected alternatives.

This applies the transferable Dutch principles—coherence, hierarchy, access and
route quality—without claiming that a Dutch authority prescribed the compiler's
decision.

## Decision answer

The compiler may legitimately draw on Dutch practice in four bounded ways:

1. model a functional hierarchy of base/access, main/strategic and
   high-quality through routes;
2. assess the network as a connected whole serving important destinations;
3. use context-sensitive mesh-width ranges as diagnostics and sensitivity
   checks; and
4. allow existing or lower-traffic streets to carry strategic cycle roles when
   they form a coherent, safe and attractive alignment.

It should explicitly reject these stronger claims:

- that the Netherlands mandates cycle connectivity at 100 m, 500 m, 1 km and
  2 km;
- that a route within 1 km is automatically a good-enough substitute;
- that every parallel route is wasteful duplication;
- that a high-level cycle route must follow a high-level motor road; or
- that Dutch grid guidance supplies population, topography or investment
  weights.

The proposed configurable 1 km candidate test is therefore compatible with the
evidence only when presented as a transparent UK compiler heuristic followed by
topological and qualitative comparison—not as a Dutch rule.
