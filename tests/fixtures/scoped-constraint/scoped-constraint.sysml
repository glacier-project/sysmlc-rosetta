package ScopedConstraint {
    private import ScalarValues::*;

    // The asserted constraint lives inside a state: rosetta rejects it
    // (substate reactors cannot see the machine's attributes).
    state def Machine {
        attribute level : Real := 1.0;
        entry;
            then idle;
        state idle {
            assert constraint levelPositive { level > 0.0 }
        }
    }
}
