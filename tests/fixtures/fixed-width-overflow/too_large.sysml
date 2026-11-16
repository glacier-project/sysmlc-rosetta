package TooLarge {
    private import ScalarValues::*;
    private import FixedWidthScalarValues::*;

    // Syside truncates the literal to UINT64_MAX (with a load warning), so
    // it is a fixture of its own: the others load without diagnostics.
    state def Machine {
        attribute u64 :> uint64 := 0;
        entry; then idle;
        state idle;
        state finished;
        transition first idle
            do assign u64 := 18446744073709551616 then finished;
    }

    // Below INT64_MIN: C cannot spell it; Python can.
    state def Negative {
        attribute l :> int64 := 0;
        attribute p : Integer := 0;
        entry; then idle;
        state idle;
        state finished;
        transition first idle
            do action {
                assign l := -9223372036854775809;
                assign p := -9223372036854775809;
            }
            then finished;
    }

    // Inside a node kind the width analysis does not evaluate.
    attribute def Box { attribute v : Integer; }
    attribute boxed : Box = new Box(18446744073709551616);
    attribute chained : Integer = (new Box(18446744073709551616)).v;
}
