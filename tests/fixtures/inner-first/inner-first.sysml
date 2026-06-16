package InnerFirst {
    private import SI::*;

    item def Ev;

    // Direct parent/child: Ev enables inner (inner -> innerB) AND a group
    // interrupt on outer (outer -> aborted). Inner-first => outer.innerB.
    state def M {
        entry;
            then outer;
        state outer {
            entry;
                then inner;
            state inner;
            state innerB;
            transition first inner accept Ev then innerB;
        }
        state aborted;
        transition first outer accept Ev then aborted;
    }

    // Three levels deep: handler at inner (scope outer::mid), group interrupt
    // on outer. Exercises the re-emit hop outer <- c_mid.
    state def MDeep {
        entry;
            then outer;
        state outer {
            entry;
                then mid;
            state mid {
                entry;
                    then inner;
                state inner;
                state innerB;
                transition first inner accept Ev then innerB;
            }
        }
        state aborted;
        transition first outer accept Ev then aborted;
    }

    // Parallel composite: region regA handles Ev; group interrupt on the
    // parallel state `region`. regB keeps running (time-driven).
    state def MPar {
        entry;
            then region;
        state region parallel {
            state regA {
                entry;
                    then a1;
                state a1;
                state a2;
                transition first a1 accept Ev then a2;
            }
            state regB {
                entry;
                    then b1;
                state b1;
                state b2;
                transition first b1 accept after 5 [s] then b2;
            }
        }
        state aborted;
        transition first region accept Ev then aborted;
    }

    // Parallel nested inside a conflicting composite: group interrupt on
    // `outer` (outer -> aborted), and region regA inside the parallel `region`
    // handles Ev. The consumed flag must propagate from regA up through the
    // parallel `region` mode to `outer`'s reactor — exercising the per-region
    // re-emit in _child_mode's PARALLEL branch. Inner-first => regA takes
    // a1 -> a2, the outer group interrupt is suppressed.
    state def MParDeep {
        entry;
            then outer;
        state outer {
            entry;
                then region;
            state region parallel {
                state regA {
                    entry;
                        then a1;
                    state a1;
                    state a2;
                    transition first a1 accept Ev then a2;
                }
                state regB {
                    entry;
                        then b1;
                    state b1;
                    state b2;
                    transition first b1 accept after 5 [s] then b2;
                }
            }
        }
        state aborted;
        transition first outer accept Ev then aborted;
    }

    // A state literally named `Ev_consumed` clashes with the generated flag.
    state def MNameClash {
        entry; then outer;
        state outer {
            entry; then inner;
            state inner;
            state Ev_consumed;
            transition first inner accept Ev then Ev_consumed;
        }
        state aborted;
        transition first outer accept Ev then aborted;
    }

    // Control: BarrierFault-style sibling composites sharing a signal (NOT a
    // conflict — the handlers live in sibling, never-co-active modes).
    state def MSibling {
        entry;
            then stateA;
        state stateA {
            entry;
                then x;
            state x;
            state y;
            transition first x accept Ev then y;
        }
        state stateB;
        state failsafe;
        transition first stateA then stateB;
        transition first stateB accept Ev then failsafe;
    }
}
