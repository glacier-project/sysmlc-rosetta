# rosetta — SysML v2 → Lingua Franca mapping reference

How the **rosetta** backend translates SysML v2 state definitions into
Lingua Franca (LF) programs targeting the Python runtime. Every supported
construct, its LF counterpart, and every deliberate rejection. The showcase
corpus under `models/showcase/` exercises all of it; the lfc-marked run
tests in `tests/backends/rosetta/test_run.py` prove the behavior.

Build one machine with:

```bash
sysmlc rosetta build models/showcase/microwave -e Microwave::Microwave -o out/
```

## 1. The big picture

| SysML | Lingua Franca |
|---|---|
| `state def M` | reactor class `M` + a trivial `main reactor` instantiating it |
| leaf `state s` | a `mode s` of its scope's reactor |
| composite `state c { … }` | child reactor class `M_c`, instantiated inside mode `c` |
| `state p parallel { region r1; region r2 }` | one reactor class per region, sibling instances inside mode `p` |
| parallel **root** machine | region instances wired at reactor scope (no modes) |
| transition | a reaction switching modes via `reset(<target>)` |
| `accept Sig` trigger | input port `Sig` (one per signal simple name) |
| `accept after t` | mode-local `timer` (literal) or scheduled `logical action` (attribute duration) |
| `attribute` | LF state variable (or reactor parameter when `in`) |
| `assert constraint` | Python `assert` checks woven into the machine reactor |
| `send` effect | self-scheduled `logical action` `<Sig>_act` |
| `then done` | `request_stop()` at the root; `completed` output in a child |
| enums / item defs | Python `Enum` / `@dataclass` classes in the file preamble |

Everything renders into **one `.lf` file**: preamble, child reactor classes
(innermost first — lfc wants definitions before use), the machine reactor,
and `main` last. Importing the file from a harness ignores its `main`.

## 2. States and hierarchy

**Leaf states** become modes. Mode entry is announced on the
`current_state` output by a `reaction(reset, startup)` whose body also runs
the state's `entry` (and inline one-shot `do`) actions, in declaration
order:

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
  reaction(reset, startup) -> current_state {= … =}
  reaction(c_cooking.current_state) -> current_state {=
    current_state.set("cooking." + c_cooking.current_state.value)
  =}
  reaction(c_cooking.completed) -> reset(idle) {= idle.set() =}
}
```

LF resets contained reactors to their initial mode when a mode is
re-entered through `reset(...)` — exactly SysML composite re-entry
semantics. Pausing and resuming the microwave restarts its heater timer
for this reason.

The composite state's own `entry`/`exit`/`do` actions run in the **parent**
reactor (mode entry reaction and transition dispatches), so they may use
the machine's attributes. Actions on substates run inside the child
reactor, which deliberately sees **no** attributes (§5).

**Parallel states** become one reactor class per region, instantiated side
by side in the parallel state's mode. A parallel **root** machine has no
modes at all: region instances and their wiring sit at reactor scope.
Region `entry`/`do`/`exit` actions run in the parent (regions enter and
leave with the parallel state).

## 3. Transitions

Within one scope, same-trigger transitions render as a single reaction
whose body is a first-match `if`/`elif` chain — declaration order is firing
priority; a guardless branch closes the chain. Each branch runs the source
state's `exit` actions, then the transition `effect`, then sets the target
mode (the target's `entry` runs at the next tag, LF's mode-switch boundary).

- **Eventless from a leaf** — folds into the entry reaction: evaluated on
  every (re-)entry of the source mode.
- **Eventless from a composite/parallel child** — *completion semantics*:
  the reaction triggers on the child's `completed` output (never on
  entry). For a parallel child it is a **join**: reactor-scope flags
  (`heating_heater_done`, …) are reset in the mode's entry reaction and
  set as each region completes; the transition fires when all are set.
- **Group interrupts** — signal/after transitions sourced AT a composite
  state are reactions of its mode in the parent: they run the composite's
  exit action and switch modes. The active substates' own exit actions do
  NOT run (they could not touch attributes anyway); contained machines are
  simply deactivated.
- **Deep exit** — a transition from inside a composite to a state in an
  enclosing scope raises a dedicated child output (`exit_0`, one per
  distinct target); each enclosing scope either resolves the target (mode
  switch) or re-raises its own exit port. The inner scope runs the source
  state's exit + the transition effect; each enclosing scope adds the
  exited composite's exit action. (Note: the effect therefore runs before
  the *outer* exits — a deliberate, documented deviation from UML's
  exit-all-then-effect ordering.)
- **Deep entry** (`idle` → `running.hot` from outside) — **rejected**:
  entering an LF mode always activates contained reactors' initial modes.
- An eventless self-loop with no event, timer, or effect is rejected as
  unstable.

## 4. Triggers and signals

Every signal accepted anywhere in the machine becomes an **input port** of
the machine reactor (event identity = the payload type's simple name).
Signals accepted inside a composite scope also become inputs of that
child reactor, and the parent's mode forwards them down
(`Tick -> c_x.Tick`). Signals are forwarded only where accepted.

- **Payload access**: `accept r : Reading` binds the payload first
  (`r = Reading.value`), so guards and effects can read `r.value`.
  Payloads are duck-typed; payload **writes** are rejected (Tier-3).
- **`accept after 4 [s]`** (literal) → a mode-local timer
  (`timer t_showRed(4 sec)`); names are state-qualified because lfc
  flattens mode-local declarations per reactor. An `after` self-loop
  re-enters the mode and restarts the timer — the periodic-tick idiom.
- **`accept after pickDuration`** (attribute) → a mode-local logical
  action scheduled on entry with the attribute's value (seconds → ns).
- **`after` + `if`** is supported (the guard is evaluated when the timer
  fires) — a capability the quake backend must reject.
- **`accept at` / `accept when`** → rejected (not yet mapped).

**`send new Sig(...) via port`** schedules a reactor-level logical action
`Sig_act` at the current tag (a self-event). If the machine also accepts
`Sig`, accepting reactions trigger on `(Sig, Sig_act)` and read whichever
is present, so external and internal events are indistinguishable. A
signal sent in one composite scope but accepted in another is **rejected**
(cross-scope event routing belongs to the parts/ports increment). The
`via` port is captured but not yet part of event identity.

## 5. Attributes

Declared on the state def (root scope only — state-scoped attributes are
rejected):

| Declaration | LF |
|---|---|
| `in attribute setpoint : Real default 21.0` | reactor **parameter** `setpoint = {= 21.0 =}` (override at `new`) |
| `attribute temperature : Real := 18.0` (also `inout`) | **state variable** `state temperature = {= 18.0 =}` |
| `out attribute …` | rejected (needs output ports — Tier 3) |
| composite attribute (`pt : Point`) | `SimpleNamespace(x=…)` initializer; usage-local `:>>` redefinitions win over the type's defaults |
| quantity (`pickDuration : DurationValue default 2 [min]`) | SI float (`120.0`) |

References render as `self.<name>` inside reaction bodies. Initializers
and parameter defaults always render inside `{= … =}` (lfc parses bare
non-literals as LF syntax). **Substate scopes see no attributes**: a guard
or action referencing a machine attribute from inside a composite state
fails loudly — author models within this limit (the bridge to per-part
state lands with the Tier-3 parts/ports family).

Initial values are **configurable at build time** with `--values FILE`
(any backend): a hierarchical YAML whose nesting mirrors qualified names.
Overrides are applied in place on the loaded model through syside's
editing API and the model is re-run through sema+validation (a fixed `=`
binding cannot be overridden; `default`/`:=` can), composite fields are
overridden per-usage without touching the type's defaults, and quantity
strings (`"90 [s]"`) are converted into the model's declared unit within
the same unit kind. See `docs/rosetta-values-constraints-design.md`.

## 6. Constraints (testbench checks)

`assert constraint name? { expr }` on the state def becomes a Python
runtime check `assert <expr>, "SysML constraint <name> violated"`, placed:

1. in a dedicated `reaction(startup)` — so invalid initial **or
   overridden** values abort the program immediately (exit code 1, the
   constraint name in the traceback), and
2. at the end of every reaction body that assigns to a machine attribute.

Plain (non-asserted) `constraint` usages generate nothing — SysML does not
require them to hold. Constraints declared inside states are rejected
(their scope has no attributes to check). The thermostat and
vending-machine showcases carry asserted invariants; the negative run test
proves a violating `--values` override aborts at startup.

## 7. Completion: `then done`

Context-sensitive:

- **Root scope**: a synthesized final mode `done` announces itself and
  calls `request_stop()` — the program winds down.
- **Child scope** (composite or region): the `done` mode announces itself
  and sets the reactor's `completed` output instead; the parent reacts per
  §3. Multiple `then done` transitions share one mode. Child reactors
  always declare `completed` (a machine that never completes simply never
  sets it).
- A parallel root joins all regions' `completed` ports into
  `request_stop()`.

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

- An enum def referenced anywhere renders as a preamble
  `class LightColor(Enum):` with literals valued by **name**
  (`red = "red"`); literals render as `LightColor.red`.
- Item defs that the machine **sends** render as preamble `@dataclass`
  payload classes (untyped fields). Harnesses drive inputs with
  `SimpleNamespace` stand-ins (payloads are duck-typed across files).
- Standard-library function calls in expressions are whitelisted:
  `abs`, `max`, `min` and `sin`/`cos`/`tan` (adding `import math`).
  Unlisted functions are rejected; extend `_FUNCTIONS` in
  `sysmlc/backends/rosetta/codegen.py` as examples demand.

## 10. Rejection summary

| Construct | Why |
|---|---|
| deep entry into a substate from outside | LF modes activate initial submodes only |
| state-scoped attributes / constraints | substate reactors see no machine state |
| `out attribute` | needs directed ports (Tier 3) |
| payload write-back (`assign r.value := …`) | Tier-3 ports family |
| `in ref` equipment references | Tier-3: equipment becomes a connected reactor |
| cross-scope sends | Tier-3 event routing |
| `accept at` / `accept when` | not yet mapped (`at` is LF-feasible) |
| long-running / non-inline `do` bodies | only inline one-shot bodies fuse into entry |
| leaf or parallel regions; transitions sourced at a region | regions must be composite; author interrupts on the parallel state |
| unstable eventless self-loops | would never stabilize |
| state names colliding with generated names (`done`, `current_state`, `completed`, ports) | rename the state |

All rejections raise `UnsupportedConstructError` loudly — rosetta never
silently drops a construct.
