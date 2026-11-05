# rosetta — SysML v2 → Lingua Franca mapping reference

How the **rosetta** backend translates SysML v2 state definitions into
Lingua Franca (LF) programs targeting the Python runtime. This document
describes supported constructs, their LF counterparts and known limitations.
Bundled model scenarios and focused tests marked `lf` compile and execute
generated programs to check selected behaviors, including hierarchy lifecycle
cases outside the showcase corpus.

Build one machine with:

```bash
sysmlc rosetta build showcase/microwave -e Microwave::Microwave -o out/
```

## 1. The big picture

| SysML                                        | Lingua Franca                                                                              |
| -------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `state def M`                                | reactor class `M` + a trivial `main reactor` instantiating it                              |
| leaf `state s`                               | a `mode s` of its scope's reactor                                                          |
| composite `state c { … }`                    | child reactor class `M_c`, instantiated inside mode `c`                                    |
| `state p parallel { region r1; region r2 }`  | one reactor class per region, sibling instances inside mode `p`                            |
| parallel **root** machine                    | region instances wired at reactor scope (no modes)                                         |
| transition                                   | a reaction switching modes via `reset(<target>)`                                           |
| `accept Sig` trigger                         | input port `Sig` (one per signal simple name)                                              |
| `accept after t`                             | mode-local `timer` (literal) or scheduled `logical action` (attribute duration)            |
| `attribute`                                  | LF state variable (or reactor parameter when `in`)                                         |
| `assert constraint`                          | Python `assert` checks woven into the machine reactor                                      |
| `send` effect                                | self-scheduled `logical action` `<Sig>_act`                                                |
| `then done`                                  | `request_stop()` at the root; `completed` output in a child                                |
| enums / item defs / composite attribute defs | Python `Enum` / `@dataclass` classes in a generated `<basename>_types.py` companion module |

The `.lf` carries the reactor classes (innermost first — lfc wants
definitions before use), the machine reactor, and `main` last; importing the
file from a harness ignores its `main`. **Generated type definitions live in
a sibling `<basename>_types.py` companion module** (one per build, named after
the artifact), shipped to the compiled program via the LF `files:` target
property; the `.lf` preamble just *imports* them. A model with no generated
types emits a bare `target Python` and no companion module. See §9.

## 2. States and hierarchy

Entry, exit and transition-effect actions support inline bodies, including
shorthand `assign`/`send`, named or anonymous untyped blocks and empty slots.
Referenced or typed wrappers are rejected with a phase-specific diagnostic,
even when they contain local statements. Only inline one-shot `do` bodies are
supported. These are compiler subset limits, not SysML source restrictions.

Inline entry, exit, do and transition-effect bodies without explicit
sequencing execute in declaration order. This is a deterministic SysMLC
convention, not an ordering requirement imposed by SysML declarations. A
complete explicit unconditional linear sequence of single-occurrence actions
takes precedence over declaration order;
supported `then` shorthands follow the same rule. Unsupported branching,
conditional sequencing, cycles and incomplete or disconnected sequences are
rejected rather than flattened into declaration order. Feature-chain endpoints
and optional or repeated multiplicities in explicit sequences are also
unsupported; explicit `[1]` multiplicity is accepted.

**Leaf states** become modes. Mode entry is announced on the
`current_state` output by a `reaction(reset, startup)` whose body also runs
the state's `entry` actions followed by its inline one-shot `do` actions,
using the body-ordering rules above:

```lf
initial mode stopped {
  reaction(reset, startup) -> current_state {=
    current_state.set("stopped")
  =}
  ...
}
```

**Composite states** become child reactor classes named
`<Machine>_<path>` (`Microwave_cooking`, `Microwave_cooking_heating_heater`
for deeper nesting). The parent renders the composite as a mode holding an
instance plus the wiring:

```lf
mode cooking {
  c_cooking = new Microwave_cooking()
  PauseCmd -> c_cooking.PauseCmd      // signal forwarding (§4)
  reaction(reset, startup) -> current_state, c_cooking._entry_order {= … =}
  reaction(c_cooking.current_state) -> current_state {=
    current_state.set("cooking." + c_cooking.current_state.value)
  =}
  reaction(c_cooking.completed) -> reset(idle) {= idle.set() =}
}
```

LF resets contained reactors to their initial mode when a mode is
re-entered through `reset(...)` — exactly SysML composite re-entry
semantics. Pausing and resuming the microwave restarts its heater timer
for this reason. Rosetta also clears its own lifecycle records (retained
triggers, selections, region-finalization flags and shared-signal gates) on
entry; §3 covers reentry of a mode that stays active.

The composite state's own `entry`/`exit`/`do` actions run in the **parent**
reactor (mode entry reaction and transition dispatches), so they may use
the machine's attributes. Actions on substates run inside the child
reactor, which deliberately sees **no** attributes (§5).

A state's entry finishes before its substates' entries begin, as required
by entry-before-middle. SysMLC also finishes supported finite inline `do`
work before activating children, as its serialization policy. Every child
reactor declares an `_entry_order` input. The parent's entry reaction lists
it as an effect, and each of the child's mode entry reactions reads it as a
non-trigger source:

```lf
reaction(reset, startup) _entry_order -> current_state {= … =}
```

LF's precedence graph then runs the parent entry reaction first at the same
tag. The port is never set and never triggers a reaction, so the ordering
adds no signal, token, reaction activation or microstep. It does add a
static input and dependency per child reactor. The root's own `entry`/`do`
is a `reaction(startup)` declared before the modes, so it runs before the
initial mode's entry in the same reactor. A state named `_entry_order` is
rejected.

**Parallel states** become one reactor class per region, instantiated side
by side in the parallel state's mode. A parallel **root** machine has no
modes at all: region instances and their wiring sit at reactor scope.
Region `entry`/`do`/`exit` actions run in the parent. In a nested parallel
state, region entry/do actions run on parallel-state entry. A completion
handler runs each region's exit when it reports readiness, even without an
eventless parent transition. Eventless parent guards wait for all required
regions to finalize; later signal guards observe the finalized region data.
A selected triggered departure of the parallel state, or of an enclosing
state, drains unfinished regions and skips finalized ones (§3). A
descendant-owned escape crossing the parallel boundary uses the same
cleanup protocol, including the origin region's exit. For a parallel root,
the root's `entry`/`do` and then each region's, in declaration order, run
in the root's `reaction(startup)`; each region's `exit` runs when that
region reports local readiness (§7). Sibling regions' substates enter in no specified
relative order.

## 3. Transitions

Within one scope, ordinary eventless alternatives or alternatives for the
same delivered signal render as a single reaction
whose body is a first-match `if`/`elif` chain — declaration order is firing
priority, including for same-source alternatives declared in different
lexical scopes (for example `transition first outer.a …` in the parent beside
`transition first a …` inside `outer`); a guardless branch closes the chain.
Each branch runs the source
state's `exit` actions, then the transition `effect`, then sets the target
mode (the target's `entry` runs at the next tag, LF's mode-switch boundary).

- **Eventless from a leaf** — folds into the entry reaction: evaluated on
  every (re-)entry of the source mode. **SysMLC leaf-entry policy:** automatic eventless
  transitions take priority over eligible time/change events, even with zero
  delay, regardless of textual declaration order. A selected branch excludes
  coincident triggered departures from that activation. False eventless
  guards leave triggered alternatives eligible.
  Quake/Sismic also prefers ordinary eventless transitions over `after 0`,
  but represents `when` transitions as eventless candidates. An enabled
  ordinary eventless transition competing with a true `when` can therefore
  raise `NonDeterminismError` in Quake; Rosetta selects the ordinary eventless
  transition during entry. This policy is not yet aligned across both backends.
- **Eventless from a composite/parallel child** — *completion semantics*:
  the reaction triggers on the child's `completed` output (never on
  entry). For a parallel child it is a **join**: reactor-scope flags
  (`heating_heater_done`, …) are reset in the mode's entry reaction. When
  a region reports readiness, the join runs its own `exit` and sets its
  flag at the same tag, without waiting for unfinished siblings; regions
  completing at the same tag exit in declaration order. Once all flags are
  set, the join evaluates the outgoing guards, so they observe the regional
  exits' assignments; only the chosen branch runs the parallel state's own
  `exit` and the transition effect. If no guard holds, the parallel state
  stays active with its regions finalized.
  A region without a scoped `done` keeps its flag false and blocks the join.
  Its unwritten `completed` port is omitted from the reaction triggers and
  body because LF's Python runtime cannot read that port.
- **Parent-sourced interrupts** — signal, `after`, `at` and `when`
  transitions sourced AT a composite or parallel state are reactions of its
  mode in the parent. The parent selects first; only a selected departure
  interrupts the active descendants, which then exit before the parent's
  own exit (see *Parent-sourced interruption* below).
- **Cross-level priority (inner-first).** When one signal enables both a
  transition inside a composite/parallel state `C` and a group interrupt on
  `C` itself, rosetta fires only the innermost — matching the SCXML/UML
  inner-first rule that the quake (sismic) backend follows. The consuming
  descendant raises a per-signal `<sig>_consumed` output inside its firing
  branch; intermediate scopes re-emit it upward (like `current_state`/
  `exit_<k>`); `C`'s group-interrupt reaction reads its boundary children's
  flags (a composite reads its own reactor's `<sig>_consumed`; a parallel
  state ORs the flags from its consuming regions) and skips its `reset()`
  when a boundary reports acceptance. Below a state with its own triggered transition, the
  flag carries the occurrence's arrival tag, so a retained ancestor
  alternative is suppressed only for that same occurrence; a false
  descendant guard leaves the occurrence to the ancestor. This plumbing is
  emitted only for detected conflicts and shared signals, so other models
  are unaffected.
- **Deep exit** — a transition from inside a composite to a state in an
  enclosing scope raises a dedicated child output (`exit_0`, one per
  distinct target); each enclosing scope either resolves the target (mode
  switch) or re-raises its own exit port. The inner scope runs the source
  state's exit + the transition effect; each enclosing scope adds the
  exited composite's exit action. (Note: the effect therefore runs before
  the *outer* exits — a deliberate deviation from UML's
  exit-all-then-effect ordering.) A composite/parallel source still drains
  its own active descendants before its exit and effect. At each crossed
  parallel boundary, unfinished regions drain in declaration order and
  finalized regions are skipped. The source path is marked as departing,
  so its exits are not repeated. A transition staying within its region
  leaves sibling regions active.
- **Deep entry** (`idle` → `running.hot` from outside) — **rejected**:
  entering an LF mode always activates contained reactors' initial modes
  (see §11 for the planned mechanism).
- An eventless self-loop with no event, timer, or effect is rejected as
  unstable.
- **Run-to-completion / declaration-order single-fire.** When several enabled
  transitions leave one state at the same logical tag (distinct signals, or a
  signal and an `after`), rosetta fires **exactly one**, chosen by **textual
  declaration order** — the first-listed outgoing transition whose trigger is
  present and whose guard holds. The mechanism is a per-mode `<state>_fired`
  flag (cleared on mode entry) that the firing reaction sets and later
  reactions at the same tag read. The losing transitions are **dropped, not
  deferred** to a next macrostep: this differs from strict run-to-completion,
  but is equivalent for every case where the winner's target does not
  re-handle the loser event. A state name colliding with a generated
  `<state>_fired` flag is rejected.

### Parent-sourced interruption

For a triggered transition sourced at a composite or parallel state, rosetta
follows an explicit SysMLC policy inspired by UML and by the correction
discussion in [KERML11-52](https://issues.omg.org/issues/KERML11-52): the
parent's guard is evaluated and a transition selected before any descendant
is interrupted. This differs from the SysML 2.0 / KerML 1.0 semantic library
(March 2026), where the state's `nonDoMiddle` precedes the outgoing
transition and its guard (`StatePerformances::TransitionPerformance`). The
issue discussion is not an adopted normative amendment. The policy is
scoped behavior of this backend, not a claim of full SysML conformance.

**Settlement, not completion.** Child reactors below such a state report
`_busy` when they select a transition and `_settled` once target entry,
recursive initial entry, eligible eventless transitions and natural region
finalization have finished. A parent trigger arriving while its subtree is
busy is retained with its payload and arrival tag and dispatched when the
subtree settles. Settlement does not wait for future events or for a child
`done`. The parent evaluates its guards once per occurrence. A false or
losing candidate is inert: it requests no exits and leaves the active
configuration unchanged. A retained occurrence whose guards are all false
is retired, not retried.

**Selected departure.** A selected parent departure runs, in order:

1. the active descendants' exits, each subtree innermost first; parallel
   regions drain in declaration order, and a naturally finalized region does
   not run its exit again;
1. the parent's own exit;
1. the transition effect, with the payload recorded at selection (the guard
   is not reevaluated);
1. target entry.

The first three steps run synchronously at the selection tag. Each child
reactor has an `_exit` request input and an `_exited` acknowledgment output,
handled by reactions declared after all modes. The LF mode switch is
declared at selection, so target entry happens at the next microstep, as
for other transitions. From then the whole subtree being left, including
sibling regions, is inactive: its timers, actions and self-sent events
cannot start ordinary work after selection. Exit bodies use the existing
finite inline action subset. Exit sends to a peer machine are delivered.
When the selected transition leaves the parent's scope, the enclosing exits
follow the effect, as for deep exits.

A descendant-owned escape also starts this cleanup at each parallel boundary
it crosses, even when that parallel state has no triggered transition of its
own. Its source exit and effect keep their existing order before enclosing
exits; sibling cleanup may interleave with the already selected source's
cleanup, while each region's exit follows its own active subtree and the
parallel state's exit follows every region. The origin acknowledges only
when requested and after its selected cleanup has finished. Early target
selection and late cleanup completion remain separate to avoid LF causality
cycles. These forced exits extend the adopted interruption policy; they do
not establish full conformance to the published SysML/KerML execution rules.
Two competing departures of the same parallel activation at one tag produce
a runtime diagnostic, including competing descendant escapes or a descendant
escape competing with a selected parent departure. Independent transitions
inside regions remain supported; the existing single-consumer rule still
resolves shared occurrences before parent fallback.

**Shared signals across regions.** When several regions of one parallel
state accept a signal, one occurrence is accepted at most once, by the
first region in declaration order with an accepting candidate. Each later
region receives the occurrence through a gate only after every earlier
region has passed on it. An earlier region holding the occurrence for
settlement delays that decision. A later region or ancestor fallback sees
the original payload and arrival tag (carried on a `<sig>_origin` input). A
state entered after the occurrence arrived does not accept it. This
arbitration applies per occurrence within one parallel state, not across
independent events or machine instances.

**Bounded retention.** A state or gate retains at most one occurrence. A
second distinct trigger arriving while one is retained, or a later event
accepted below a state that retains an older one, stops execution with a
runtime error instead of reordering or dropping events. This conservatively
includes a new trigger at exactly the tag where the subtree settles. There
is no general event queue. Repeated sends on one LF port at one tag can
coalesce, and the arrival tags cannot distinguish or recover them, so
lossless same-tag transport of repeated events is not provided.

**Reentry.** Leaving and later reentering a state relies on LF's mode reset
of child reactors, `reset state` variables, timers and suspended actions,
plus the entry resets of the lifecycle records above. The LF 0.13 Python
target retains scheduled events when a reset mode stays active, contrary to
the [documented reset semantics](https://www.lf-lang.org/docs/writing-reactors/modal-models/).
This behavior also reproduces with the 2026-11-05 LF 0.14.0 nightly.
For a state that a self transition or a transition to an enclosing state
can reenter while active, each `after` and `at` entry records its due time
as a workaround; a callback runs only at that
time and consumes the record before evaluating the guard, so stale or
duplicate callbacks are ignored. `when` latches are reset on entry. Events
self-sent or routed by a previous activation are still delivered after
reentry.

**Limits.** A shared occurrence held in a region left by a descendant-owned
escape stays unresolved until the parallel state is reentered; a new
occurrence before then meets the overlap error. This is outside the
parent-interruption guarantees. Cross-scope sends within one machine stay
unsupported, and zero-delay peer feedback into the same machine remains an
LF causality cycle; arbitrary routed feedback topologies are not proven
acyclic. Generated lifecycle ports and state variables that collide with
state, signal or root attribute names are rejected.

## 4. Triggers and signals

Each known concrete signal type conforming to an accept becomes an **input
port** of the machine reactor. Identity is the resolved qualified type;
unambiguous simple external names stay unchanged. A compile-scoped catalog
includes loaded subtypes even when no action constructs them. Same-source
matching accepts use declaration order: the first true guard (or guardless
accept) wins, without specificity sorting. One send produces one concrete
event, with no additional ancestor events. `LfProgram.signal_channels` records
the qualified identity/channel mapping; `resolve_signal` accepts qualified or
unambiguous simple external names and diagnoses ambiguity.
Signals accepted inside a composite scope also become inputs of that
child reactor, and the parent's mode forwards them down
(`Tick -> c_x.Tick`). Signals are forwarded only where accepted.

- **Payload access**: `accept r : Reading` maps the source payload feature
  to the delivered value, so guards and effects can read `r.value`. Different
  transitions may use different aliases, including an alias whose spelling
  matches a machine attribute; references retain their source identities.
  Payloads are duck-typed; payload **writes** are not currently supported
  (planned via copy-on-accept semantics — §11).
- **`accept after 4 [s]`** (literal) → a mode-local timer
  (`timer t_showRed(4 sec)`); names are state-qualified because lfc
  flattens mode-local declarations per reactor. An `after` self-loop
  re-enters the mode and restarts the timer — the periodic-tick idiom.
- **`accept after pickDuration`** (attribute) → a mode-local logical
  action scheduled on entry with the attribute's value (seconds → ns).
- **`after` + `if`** is supported (the guard is evaluated when the timer
  fires) — a capability the quake backend must reject.
- **`accept at t`** → a mode-local logical action scheduled on entry for
  the instant `t`, measured as logical time since startup; an instant that
  has already passed at entry schedules nothing.
- **`accept when cond`** → a change check: entry schedules `_change_act`,
  and every reaction assigning a root attribute reschedules it one
  microstep later. The first true observation in an activation sets a
  per-state latch (reset on entry) and evaluates the transition once.
  This lowering supports source states in the root reactor and conditions
  over root attributes; change triggers in nested reactors are not supported.
  Checks observe values when they run, so a transient true value overwritten
  before the scheduled check can be missed.

**`send new Sig(...) via port`** schedules a reactor-level logical action
`Sig_act` at the current tag (a self-event). If the machine also accepts
`Sig`, accepting reactions trigger on `(Sig, Sig_act)` and read whichever
is present, so external and internal events are indistinguishable. A
signal sent in one composite scope but accepted in another is **not
currently supported** within a single machine (§11). Signals that cross
*between* machines, in contrast, already route through ports in a composed
build (§10). The `via` port is captured but not yet part of event identity.

## 5. Attributes

Declared on the state def (root scope only — state-scoped attributes are
not currently supported; scope-local support is planned, §11):

| Declaration                                               | LF                                                                                                                                                         |
| --------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `in attribute setpoint : Real default 21.0`               | reactor **parameter** `setpoint = {= 21.0 =}` (override at `new`)                                                                                          |
| `attribute temperature : Real := 18.0` (also `inout`)     | **state variable** `state temperature = {= 18.0 =}`                                                                                                        |
| `out attribute …`                                         | not currently supported (planned, §11)                                                                                                                     |
| composite attribute (`pt : Point`)                        | `Point(x=…)` dataclass initializer (the `Point` dataclass lives in the companion module, §9); usage-local `:>>` redefinitions win over the type's defaults |
| quantity (`pickDuration : DurationValue default 2 [min]`) | SI float (`120.0`)                                                                                                                                         |

References render as `self.<name>` inside reaction bodies. Initializers
and parameter defaults always render inside `{= … =}` (lfc parses bare
non-literals as LF syntax). **Substate scopes see no attributes**: a guard
or action referencing a machine attribute from inside a composite state
fails loudly — author models within this limit (per-scope attributes are
planned, §11).

Bound values (`=`) pass shared admission checks before rendering. Supported
forms are Boolean, Integer, Rational and String literals, unary numeric signs,
symbolic enumeration literals, enumeration literals whose projected values
satisfy the same literal subset, and literal quantities with standard-library
units.
Dynamic bindings and other expressions, including function calls with constant
results, are unsupported. Whole-value bindings of structured attributes are
also rejected; their fields are otherwise materialized individually.

Whole-value assignments must preserve fixed fields. When the type fixes a field
with `=`, an unverified replacement such as a calculation result is rejected;
a compatible referenced value passes this admission check. Fixed fields on the
particular usage, including configuration-created fields, prevent whole-value
replacement. Default fields can be overridden by explicit new values. Constructor
expressions as assignment values remain unsupported.

Assignments to effective bound features are rejected in entry, exit, do and
transition-effect actions, including assignments of the same value. This is a
SysMLC supported-subset restriction, not a general SysML prohibition on
assignments consistent with an equality. `default =` remains overridable before
instantiation and remains a binding when it applies. `:=` and `default :=`
provide initial values that may subsequently change. Use `:=` in place of `=`
only when initialization is the intended semantics. These checks do not
provide general enforcement of the separate `constant` modifier or arbitrary
mutations by external Python code.

Initial values are **configurable at build time** with `--values FILE`
(any backend): a hierarchical YAML whose nesting mirrors qualified names.
Overrides are applied in place on the loaded model through syside's
editing API and the model is re-run through sema+validation. A fixed `=`
binding cannot be overridden; `default`/`:=` can. Composite fields are
overridden per-usage without touching the type's defaults, and quantity
strings (`"90 [s]"`) are converted into the model's declared unit within
the same unit kind.

## 6. Constraints (runtime checks)

`assert constraint name? { expr }` on the state def becomes a Python
runtime check `assert <expr>, "SysML constraint <name> violated"`, placed:

1. in a dedicated `reaction(startup)` — so invalid initial **or
   overridden** values abort the program immediately (exit code 1, the
   constraint name in the traceback), and
1. at the end of every reaction body that assigns to a machine attribute.

Plain (non-asserted) `constraint` usages generate nothing — SysML does not
require them to hold. Constraints declared inside states are not currently
supported (their scope has no attributes to check; planned with scope-local
attributes — §11). The thermostat and vending-machine examples carry
asserted invariants; a negative run test proves a violating `--values`
override aborts at startup.

## 7. Completion: `then done`

Context-sensitive:

- **Root scope**: a synthesized final mode `done` announces itself, runs
  the root's own `exit` action, and calls `request_stop()` — the program
  winds down. The root exit runs only on this completion, after the
  completing transition's source exit and effect.
- **Child scope** (composite or region): the `done` mode announces itself
  and sets the reactor's `completed` output instead; the parent reacts per
  §3. Multiple `then done` transitions share one mode. Child reactors
  always declare `completed` (a machine that never completes simply never
  sets it).
- A parallel root joins all regions' `completed` ports. A region's
  `completed` only reports that its middle has finished; the join then
  runs that region's own `exit` and marks the region finalized, at the
  same tag and without waiting for unfinished siblings. Regions completing
  at the same tag exit in declaration order, a SysMLC deterministic
  refinement. A tag includes the logical time and microstep. Once every region is
  finalized, the join runs the root's `exit` action and calls
  `request_stop()`. A partial join runs no root exit.
- Host shutdown (a run timeout, an empty event queue or a peer machine's
  `request_stop()`) only ends observation: it never runs a root or region
  exit or interrupts active descendants, so a machine still waiting for
  input is not exited. A root or
  parallel-root region exit without a completion path is still checked against the
  supported subset at compile time, but never executes.

## 8. Observability

Every reactor (machine and children) announces mode entries on a
`current_state` output (`string`). Parents re-emit child announcements as
dotted paths (`cooking.heating.heater.warming`); parallel regions announce
independently with the region name interposed. Within one tag the LAST
`set` wins deterministically (reaction declaration order), so on composite
entry observers see the full dotted path directly, and when parallel
regions step at the same tag only the last-declared region's announcement
is visible — pin sequences accordingly in tests.

## 9. Enumerations, payload classes, functions

Generated types (enums, sent-item dataclasses, composite attribute-def
dataclasses) are emitted into a sibling **`<basename>_types.py` companion
module**, NOT inline in the preamble. The `.lf` preamble holds only
`import <basename>_types as _sysmlc_types` (plus stdlib/`--python` imports), and
the `.lf` target header lists the module in `files:` so `lfc` copies it into
`src-gen` (importable at runtime with no `PYTHONPATH`). The module is
generated whenever ≥1 type exists; a type-less model emits neither the module
nor a `files:` entry. A `--python` file may import this module when it needs
the concrete generated class. It may also operate structurally: the shared
Furuta support module reconstructs `type(x)`, which keeps it compatible with
different backend companion-module names without duplicating the physics.

- An enum def referenced anywhere renders as a companion-module
  `class LightColor(Enum):` with literals valued by **name**
  (`red = "red"`); literals render as `_sysmlc_types.LightColor.red`.
- Concrete accepted and sent item defs, and composite `attribute def`s used as
  attribute values, render as companion-module `@dataclass` classes. Fields
  are typed by mapping SysML scalars (`Real`→`float`, `Integer`→`int`,
  `Boolean`→`bool`, `String`→`str`, else `object`) and carry the model's
  effective default, including inherited fields and defaults replaced through
  redefinition. Unspecified fields default to `None`; fields whose values
  need runtime construction are supplied by keyword. A nonliteral class
  default, such as an enum member, makes that field required and the class
  keyword-only.
- Standard-library function calls in expressions are whitelisted:
  `abs`, `max`, `min` and `sin`/`cos`/`tan` (adding `import math`).
  Unlisted functions are rejected; extend the shared library-function mapping
  in `sysmlc/codegen/python.py` as needed.

## 10. Composing two machines (in-model testing)

A model's test scenario can live in SysML as a second `state def` paired
with the machine under test by a small `part def`. The rosetta backend then
composes **both** machines into one LF program: one machine drives stimuli
the other accepts, observes the other's sends, and decides a verdict — with
no scripted harness. The two machines are **symmetric**; there is no fixed
"machine" / "test" role in the mechanics, only the two usage names, which
become the LF instance names.

The primary composition path is **port-based parts with explicit
`connect`** (§13), which the example models use. The mechanism described in
this section is the **same-name** composition used when a single `part def`
holds two or more `exhibit state` usages; it is reachable by selecting such a
part directly (`-e P::XTestRig`). Both paths are deterministic — LF
determinism does not depend on how the connection graph is formed.

```sysml
state def Microwave { … }              // the machine under test
state def MicrowaveTest { … }          // stimuli + verdict
part def MicrowaveTestRig {
    exhibit state subject : Microwave;
    exhibit state test : MicrowaveTest;
}
```

A `part def` used this way holds **two or more** named `exhibit state`
usages, each typed by a state def declared in the model (documentation
aside). The two usage names become the LF instance names.

**CLI.** No special flag. `-e P::MicrowaveTestRig` selects the composition;
`-e P::Microwave` still builds the bare machine. With no `-e`, a model
declaring exactly one such part auto-selects it. The build emits **one
file** named after the part, holding both machines' reactor families, the
composing reactor, and the trivial main; the standing "compile through an
importing app" limitation (§1) is unchanged. `--values` carries sections for
both state defs in one YAML and is applied per machine as two
`configure_model` passes.

### Send → port classification

A signal a machine sends is not always a self-event. Knowing what the *peer*
accepts (gathered by a signal-interface scan over each machine), each sent
signal classifies:

| Sent signal is…               | Generated form                               |
| ----------------------------- | -------------------------------------------- |
| accepted locally only         | self-event `Sig_act.schedule(0, …)`          |
| peer-accepted only            | `output Sig` port; send → `Sig.set(payload)` |
| locally **and** peer-accepted | **both statements** (overlap case)           |
| accepted by nobody            | void self-event                              |

Types without application fields render `Sig.set(True)`; data-bearing types
render `Sig.set(Sig(…))`, including constructors with no explicit arguments.
Positional and named arguments bind to their resolved fields and evaluate
once at the send in constructor order. Omitted fields carry effective constant
defaults, including inherited and redefined defaults: with `x default 7` and
`y default 9`, `new Data(y = 42)` constructs `Data(y=42, x=7)`, and
`new Data()` constructs `Data(x=7, y=9)`. Omitted structured fields materialize
their nested values; unspecified fields retain their generated class
placeholders. Nonconstant omitted defaults are diagnosed and must be passed
explicitly as constructor arguments. Self-events construct the same values
in `Sig_act.schedule(0, Sig(…))`.

Constant eligibility does not imply expression-renderer support: a direct
quantity default such as `default 0.5 [s]` is rejected with an unsupported
`Quantity` operator diagnostic during send emission. This is separate from
scalar-quantity state-machine initialization, which converts values to SI
magnitudes before rendering.

Catalog-backed payload constructors and enum references use the reserved
`_sysmlc_types` module alias, including nested structured values. Direct
catalog-free builders establish their companion module identity before code
generation and also import bare type symbols for catalog-free composite values. Qualified definitions with
colliding simple names receive distinct generated class symbols; class symbols
are independent of channel names. Unresolved collisions with generated LF
identifiers produce explicit diagnostics.

### Input ports and up-chaining

LF forbids an input and an output sharing a name, so a machine's **inputs =
accepted signals − peer-sent signals**. The subtracted input could never be
fed (a signal sent by *both* machines is rejected, §11); in the overlap case
the local accept triggers on `Sig_act` alone.

Peer-accepted sends relax the within-machine cross-scope rejection (§4)
**outward only**: a send inside a nested composite gives the innermost
reactor the `output Sig` port, and every enclosing reactor declares the same
output and forwards the child's (`Sig.set(c.Sig.value)`) — the same
up-chaining mechanism as `exit_<k>` and dotted `current_state`. Cross-scope
sends *within* one machine stay unsupported (§11).

### Composing reactor, wiring, and verdicts

The composing reactor instantiates both machines under their usage names and
wires same-named signal ports in **both directions**, plus forwards each
machine's `current_state` out as `<usage>_current_state`:

```lf
reactor MicrowaveTestRig {
  output subject_current_state
  output test_current_state
  subject = new Microwave()
  test = new MicrowaveTest()
  test.StartCmd -> subject.StartCmd      // per matched signal, both directions
  subject.Finished -> test.Finished
  subject.current_state -> subject_current_state
  test.current_state -> test_current_state
}
```

The composing reactor is last in the program, so `program.reactor`, the
`write()` basename, and `summary()` keep working; the trivial main
instantiates it.

**Verdicts need no roles.** An `assert constraint` violation in *either*
machine aborts the run with exit 1 naming the constraint (an `AssertionError`
in an LF Python reaction kills the program); either machine's root
`then done` → `request_stop()` ends the run. A common idiom is a `fail`
state assigning `verdict := 1` under
`assert constraint testPassed { verdict == 0 }`, with `then done` on the
happy path.

### Authoring note: the transient latch

lfc 0.11 rejects a causality cycle when an announcement reaction would both
**read a routed input** and **write an output** at the same tag (a modal
self-loop through the routed port). When a verdict needs such an
announcement, defer the send: route through an eventless completion
transition into a transient state whose `entry` does the `send`, so the
output is set one microstep later, after the input reaction settled. The
furuta-pendulum and milling-workcell test scenarios use this latch;
milling's also omits the `Shutdown` stimulus for the same reason (shutdown
stays covered by the standalone milling run tests).

## 11. Rejection summary

All rejections raise `UnsupportedConstructError` loudly — rosetta never
silently drops a construct. They fall into two groups: constructs that are
**not yet supported but planned** (a mapping has been worked out, but the
implementation has not landed) and constructs that **stay rejected** by
design.

### Not yet supported (planned)

| Construct                                  | Intended mapping                                                                                                                                                                                                                                                                                                     |
| ------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| intra-machine cross-scope sends            | the sending scope's child reactor gains an output port; the parent wires it to the accepting scope (the cross-machine ported-send machinery of §10, pointed inward)                                                                                                                                                  |
| `out attribute`                            | output port, set on every assignment; doubles as observation for testing                                                                                                                                                                                                                                             |
| leaf regions                               | auto-wrapped into a one-mode region reactor (`state beeper;` as a region just works)                                                                                                                                                                                                                                 |
| transitions sourced at a region            | deterministic interrupt on the parallel scope: declaration order is firing priority across the scope; on firing, the parallel-state-sourced interruption sequence of §3 applies (regions drain in declaration order, then the parallel state's exit, the effect and the switch), so the two spellings are equivalent |
| payload write-back (`assign r.value := …`) | copy-on-accept: every `accept` binds a fresh copy, so mutations stay local to the receiver and mutate-and-resend works; LF determinism preserved once cross-machine ports exist                                                                                                                                      |
| state-scoped attributes / constraints      | scope-LOCAL only: an attribute declared inside a composite becomes a state variable of that child reactor, usable in that scope's guards, actions, and constraints (lifts the "data logic at root scope only" authoring rule). Cross-scope visibility remains future work                                            |
| deep entry into a substate from outside    | a synthesized `enter_at` dispatch per scope, mirroring deep exit's `exit_k` ports, recursive for arbitrary depth; UML's outer-then-inner entry order preserved                                                                                                                                                       |

### Stays rejected

| Construct                                                                                                     | Why                                                                                                |
| ------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `in ref` equipment references                                                                                 | belongs with the parts/ports family: equipment becomes a connected reactor                         |
| nested-parallel regions (a region that is itself `parallel`)                                                  | wrap it in a composite state                                                                       |
| long-running / non-inline `do` bodies                                                                         | only inline one-shot bodies fuse into entry                                                        |
| unstable eventless self-loops                                                                                 | would never stabilize — a model error                                                              |
| state names colliding with generated names (`done`, `current_state`, `completed`, ports)                      | rename the state; plain generated names keep the LF readable                                       |
| `in` attribute without a default                                                                              | LF reactor parameters require one                                                                  |
| a signal sent by **both** machines of a composition (bidirectional same name)                                 | LF port direction clashes; neither side can keep the matching input — split the name per direction |
| a composing part not exhibiting **two or more named** state defs (anonymous exhibit, or a non-exhibit member) | name every exhibit                                                                                 |
| the **same state def exhibited twice** in one part                                                            | a composition combines distinct machines                                                           |
| cross-machine reactor **name collisions** across a composition's machines                                     | reactor names remain keyed by simple name — rename a state or machine                              |
| a Python **textual representation** outside a package or calc def                                             | its text would execute at import time in the generated module (§12)                                |
| a rep body that is not valid Python, or whose `def` does not match the calc def's name                        | fails at build naming the calc def, instead of at run time (§12)                                   |
| **two Python reps** on one calc def, or two rep bodies defining the **same function differently**             | the generated module's namespace is flat; the last definition would silently win (§12)             |

## 12. Function calls

### Builtin functions in expression positions

A whitelist of standard-library functions is resolved in **expression
positions** — guards and assignment RHS — via the shared library-function
mapping in `sysmlc/codegen/python.py`:

| SysML qualified name      | Python rendering                   |
| ------------------------- | ---------------------------------- |
| `NumericalFunctions::abs` | `abs(…)`                           |
| `NumericalFunctions::max` | `max(…)`                           |
| `NumericalFunctions::min` | `min(…)`                           |
| `TrigFunctions::sin`      | `math.sin(…)` (adds `import math`) |
| `TrigFunctions::cos`      | `math.cos(…)`                      |
| `TrigFunctions::tan`      | `math.tan(…)`                      |

Unlisted functions are rejected; extend the shared mapping as new cases demand.

### Assignment-from-call

The only form in which a function call appears as a transition effect is an
**assignment-from-call** — the RHS of `do assign x := …`:

```sysml
do assign x := NumericalFunctions::max(x, 0.0)
```

renders as:

```python
self.x = max(self.x, 0.0)
```

The RHS routes through `_emit_invocation` in `LfPythonCodeGen`, which
resolves the callee against the shared library-function mapping or the
external registry (below).

**Functions cannot be bare `do` effects.** A SysML `calc def` can only be
invoked in an expression position; syside rejects a standalone
`do log(...)` statement effect with
`perform-action-usage-reference: A perform action must reference an action usage`. Any I/O or observation must therefore be backend-generated, not a
user-written `do` call.

### The `sysmlc` utility library

`sysmlc/sysml/lib/sysmlc.sysml` ships two utility **functions** (declared
as `calc def`, like `NumericalFunctions::abs` — not `action def`):

```sysml
package sysmlc {
    calc def print { in msg; }
    calc def log { in tag; in value; }
}
```

`load_model` always prepends bundled libraries (libraries first), so any
model can reference `sysmlc::print`/`sysmlc::log` without an explicit
import. The library is **not yet mapped by rosetta** — `print`/`log` are
reserved for the observation layer, where the backend generates the logging
reaction; the user never writes the invocation.

### External functions (`--python`)

A `calc def` declared in SysML can be backed by a user-supplied Python file
at build time:

```sysml
package P { calc def step { in x : Real; in dt : Real; return : Real; } }
state def Ramp {
    attribute x : Real := 0.0;
    …
    transition … do assign x := P::step(x, 0.1) then run;
}
```

```bash
sysmlc rosetta build models/furuta -e Furuta::Pendulum -o out/ --python physics.py
```

The CLI:

1. Parses `physics.py` with `ast.parse` and collects top-level `def` names.
1. Passes `(module_stem, names)` into the build; codegen registers them on
   `PreambleNeeds.register_external`.
1. Matches the invoked `calc def` **by simple name** (e.g. `step`) against
   the registered set; adds each hit to `used_external`.
1. Emits `from <module> import <name>` in the `.lf` preamble for each used
   name, and calls the function as `step(self.x, 0.1)` — a bare
   unqualified call.
1. **Copies `physics.py` next to the generated `.lf`** and lists it in the
   `.lf`'s `files:` target property (alongside the companion types module),
   so `lfc` copies it into `src-gen` and the binary imports it at runtime.
1. `--python` is also accepted by quake state-definition builds. Quake
   serializes the import in the sismic preamble but does not copy the module,
   because it does not generate a self-contained program like rosetta's `.lf`
   output.
1. A `calc def` with **no backing function** in the `--python` module fails
   loud, naming the function and the module (not the generic "unsupported
   function" error).

Functions that exchange a structured type (e.g. `step(x : PendulumState) → PendulumState`) import that type from the generated companion module
(`from <basename>_types import PendulumState`); the model is the single source
of the type, so there is no drift between the SysML `attribute def` and the
Python side. Construct the type by name (`PendulumState(theta=…, …)`) — the
module is on the path at runtime via `files:`.

**Purity / determinism contract.** An external function MUST be a pure,
deterministic function of its inputs — no `random`, no wall-clock time, no
hidden global state. LF's logical-time model guarantees deterministic
reaction scheduling; a side-effecting or non-deterministic function breaks
that guarantee. This contract is the model author's responsibility; the
toolchain does not enforce it.

**Deployment (`files:`, not `PYTHONPATH`).** The `--python` module and the
generated companion types module are both listed in the `.lf`'s `files:`
target property, so `lfc` copies them into `src-gen` and the compiled binary
imports them with **no `PYTHONPATH`**. (`lfc` 0.11 honors `files:` declared on
an *imported* reactor file too, so a harness importing the machine needs no
`files:` of its own.) The compiled project is therefore self-sufficient:
`lfc Main.lf && ./bin/Main` works with a clean environment, proven by
`tests/.../test_run.py::test_part_runs_self_sufficiently_without_pythonpath`.
The DEBUG-observation `sitecustomize.py` still rides on `PYTHONPATH` (that is
the run enabling logging, not an import requirement).

### Inline functions (textual representations)

Instead of shipping a separate `--python` file, a `calc def` can carry its
own Python body inline, as a SysML **textual representation**:

```sysml
package P {
    calc def step {
        in x : Real;
        in dt : Real;
        return : Real;

        rep impl language "Python"
        /*
         * def step(x, dt):
         *     return x + dt
         */
    }
}
```

The body is the **whole function**: signature, body, and return. The `def`
must be named after its calc def, because call sites and the preamble import
resolve by simple name (the same rule `--python` modules follow). Syside
normalizes the comment body before it reaches the generator: the delimiters,
the per-line `*` decoration, and the common indentation margin are stripped,
while relative indentation is preserved.

When no `--python` module is supplied, the CLI collects every Python rep in
the model into one generated module named `<element>_impl.py` and feeds it
through the `--python` pipeline itself: the written file is parsed for its
top-level function names, copied beside the `.lf`, and listed in `files:`
exactly like a user-supplied module. Because this happens at the CLI, not
inside a backend, reps back every backend that accepts `--python`: quake
builds and runs consume them the same way. Building
`sm-examples/sm15-rep` (the inline twin of `sm15-external`) produces:

```python
# Ramp_impl.py (generated)
# Auto-generated from SysML TextualRepresentation bodies.
# Do not edit — regenerate from the SysML source instead.

def step(x, dt):
    return x + dt
```

```
target Python {
  files: ["Ramp_impl.py"],
}

preamble {=
  from Ramp_impl import step
=}
…
      self.x = step(self.x, 0.1)
```

An explicit `--python` module **wins**: reps back the calc defs only when no
external module is given, so a hand-written module can temporarily override
the model's inline bodies without touching the model.

**Module scaffolding lives on the package.** Shared imports, constants, and
private helpers are written once, in a rep attached to the **package**; they
are collected ahead of the function bodies, so the generated module reads
like a hand-written one:

```sysml
package FurutaPendulum {
    rep scaffolding language "Python"
    /*
     * import math
     *
     * ALPHA = 0.00260569
     *
     * def _sign(x):
     *     return float((x > 0.0) - (x < 0.0))
     */

    calc def restrict_angle {
        in theta : Real;
        return : Real;

        rep impl language "Python"
        /*
         * def restrict_angle(theta):
         *     pi = math.pi
         *     return (math.fmod(math.fabs(theta) + pi, 2 * pi) - pi) * _sign(theta)
         */
    }
}
```

Because collection is model-wide (not call-site-driven), every rep-backed
calc def lands in the same module: a body may freely call another rep-backed
calc def, the way `swingup_torque` calls `restrict_angle` in
`showcase/furuta-pendulum/nondeterministic`. The sibling `deterministic`
variant uses the shared external `furuta_physics.py` instead. Scaffolding
helpers enter the external-name set like any function in a `--python` module,
but a SysML call site can only reference a `calc def` declared in the model,
so a helper backs a call only when a calc def of that name exists.

The purity / determinism contract of `--python` applies unchanged: a rep
body must be a pure, deterministic function of its inputs.

**Boundaries.** The collector fails loud, naming the offending element, on:

| Rejected                                                  | Why                                                                                                        |
| --------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| a Python rep on anything but a package or a calc def      | its text would land at module top level and execute at import time                                         |
| a rep body that does not parse as Python                  | the error names the calc def and the Python line, instead of a misleading missing-function error later     |
| a body whose `def` does not match the calc def's name     | the preamble imports the calc def's name; a mismatch would only fail at run time                           |
| two Python reps on one calc def                           | the function body would be ambiguous; keep exactly one (reps in other languages coexist fine)              |
| two rep bodies defining the same function **differently** | the module namespace is flat, so the last `def` would silently win; byte-identical duplicates stay allowed |

## 13. Parts, ports, and the generated main reactor

A SysML **part** is the structural unit the backend turns into LF reactors.
The part assembler (`sysmlc_rosetta/parts.py`, `build_part_program`)
walks the part graph (`sysmlc/semantics/parts/graph.py`) and emits one reactor
class per part def plus an explicit `main reactor`.

| SysML                                     | LF                                                                        |
| ----------------------------------------- | ------------------------------------------------------------------------- |
| `part def Foo { exhibit state : Beh; }`   | `reactor Foo { … }` (the inlined machine `Beh`, named after the part def) |
| nested `part f : Foo;` in a usage         | `f = new Foo()` inside `main reactor`                                     |
| top-level **part usage** `part sys { … }` | the `main reactor`                                                        |
| `connect a.pa to b.pb;`                   | LF connections for the signals that cross those ports (§13.1)             |
| CLI `--fast` / `--timeout "5 sec"`        | `target Python { fast: true, timeout: 5 sec, }` header                    |

A part def with **1 exhibit** inlines that machine; **≥2 exhibits** compose
into a reactor that instantiates each exhibit as a named child and same-name
cross-wires them (§13.5). **0** exhibits with nested parts (a deep composite)
is not currently supported. A part def reused by several parts builds its
reactor **once** and is instantiated per usage.

### 13.1 Connections are port-based (not name-based)

Routing follows the **connected ports**, honoring the SysML model — not a
global same-name auto-wire. For `connect a.pa to b.pb`, a signal `S` crosses
**only if** one end *sends* `S` `via pa` and the other *accepts* `S` `via pb`:

```sysml
part def Pinger { port commPort; exhibit state : PingerBehavior; }   // sends Ping via commPort
part def Ponger { port commPort; exhibit state : PongerBehavior; }   // sends Pong via commPort
part pingSystem { part a : Pinger; part b : Ponger;
                  connect a.commPort to b.commPort; }
```

→

```
main reactor {
  a = new Pinger()
  b = new Ponger()
  a.Ping -> b.Ping
  b.Pong -> a.Pong
}
```

The port-aware interface (`MachineInterface.accepted_via` / `sent_via`,
`sysmlc/semantics/statemachine/interface.py`) supplies the per-port signal
sets. The send-side `via` port is `SendActionUsage.sender_argument.referent`;
the accept-side is `Trigger.via_port`. The LF input/output ports are still
named by **signal** (`Ping`, `Pong`); the SysML port scopes *which* signals a
connection carries — so a part with two ports routes each port independently
(a signal sent via one port does not leak to a peer on the other).

### 13.2 Strict validation & rejections

- A `connect` naming a part or port that the model does not declare → rejected.
- A behavior that `send/accept`s `via` a port the part def does not declare →
  rejected (the inlined machine's ports must resolve against the part's).
- **Single-channel fan-in** — two sources into one input port (the same LF
  destination) → rejected, pointing at multiplicity (banks/multiports, not
  yet supported).
- **Bidirectional same-name** signal over one connection → rejected.

### 13.3 Observation (auto entry/exit DEBUG logging)

With `RosettaBuilder(observe=True)` (set only by the part assembler), every
state's mode logs its entry and exit:

```python
logging.debug("entered <Reactor>.<state>")   # in the entry reaction
logging.debug("exited <Reactor>.<state>")     # in each leaving transition
```

`PreambleNeeds.uses_logging` adds `import logging`. The program is **silent by
default** (the root logger is unconfigured at WARNING); the *run* enables
DEBUG — `run_all.py` and the lf tests drop a `sitecustomize.py` doing
`logging.basicConfig(level=logging.DEBUG)` on `PYTHONPATH`, so the entry/exit
lines reach stderr without the generated program forcing them on. The bare
machine and same-name composition paths leave `observe` off; only the part
assembler sets it.

### 13.4 Status and current limitations

Supported: single- and multi-exhibit composition; a generated `main reactor`
for a top-level part usage with port-based `connect`; observation via the
DEBUG entry/exit trace; and closed-loop simulation with external physics (the
furuta-pendulum model, §13.5).

Not yet supported (each a separate future addition):

- **banks / multiports** — the multiplicity fix for single-channel fan-in
  (§13.2). The LF plumbing has been validated, but how a state machine
  *consumes* a width-N multiport needs its own design.
- **deep composite parts** — a part with both an inline exhibit *and* nested
  parts.
- **per-port signal scoping beyond routing.**

### 13.5 Multi-exhibit composition, external functions, reset-state

- **≥2-exhibit parts** (`compose_exhibits`, `parts.py`): each exhibit → a named
  child reactor; same-named signals cross-wired among all exhibits (name-based),
  each `current_state` forwarded as `<exhibit>_current_state`; fan-in of one
  signal into a common accepter and bidirectional same-name are rejected. A
  ≥2-exhibit part is **self-contained** — its exhibits' `via` ports are
  internal, so it does not participate in port-based `connect` routing.
- **External functions for part usages**: `--python FILE` works when building a
  top-level part usage (`build_part_program(…, external=…)`), not just bare
  machines — the furuta-pendulum closed loop uses it for the pendulum physics.
- **`reset state` for join flags**: a parallel composite's join-completion flags
  render as LF `reset state` (not plain `state`) so lfc accepts the child reactor
  when it is instantiated inside a `reset` mode (the flags correctly reset on
  composite re-entry).
