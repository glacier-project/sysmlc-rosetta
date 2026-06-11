// `accept after` combined with an `if` guard: rosetta supports this
// (the timer fires once and the guard is evaluated at that instant);
// quake must reject the same construct (sismic's `after` is monotonic).
package AfterGuard {
    private import SI::*;
    private import ScalarValues::*;

    state def Machine {
        attribute ready : Boolean := true;
        entry;
            then idle;
        state idle;
        state running;
        transition first idle accept after 5 [s] if ready then running;
        transition first running then done;
    }
}
