package NegatedConstraint {
    private import ScalarValues::*;

    // A negated root-scope assertion: the expression must be false.
    state def Machine {
        attribute level : Real := 1.0;
        assert not constraint tooHigh { level > 2.0 }

        entry;
            then idle;
        state idle;
    }
}
