package PerWriteChecks {
    private import ScalarValues::*;
    private import NumericalFunctions::*;
    item def Go;
    item def Pt { attribute x : Integer; }

    // Two writes in one effect: a check after each, none after the rest.
    state def Twice {
        attribute count : Integer := 0;
        assert constraint capped { count <= 200 }
        entry; then idle;
        state idle;
        transition first idle accept Go
            do action { assign count := count + 1; assign count := count + 1; }
            then idle;
    }

    // The constraint mentions `count` only; `other` is written alone.
    state def Unrelated {
        attribute count : Integer := 0;
        attribute other : Integer := 0;
        assert constraint capped { count <= 200 }
        entry; then idle;
        state idle;
        transition first idle accept Go do assign other := 1 then idle;
    }

    // The write sits in a guarded effect.
    state def Branchy {
        attribute count : Integer := 0;
        assert constraint capped { count <= 200 }
        entry; then idle;
        state idle;
        state finished;
        transition first idle accept Go if count < 5
            do assign count := count + 1 then idle;
        transition first idle accept Go then finished;
    }

    // `inOrder` mentions both attributes; the effect breaks it in between.
    state def Pair {
        attribute low : Integer := 0;
        attribute high : Integer := 5;
        assert constraint inOrder { low <= high }
        entry; then idle;
        state idle { entry action { assign low := 10; assign high := 20; } }
        state finished;
        transition first idle then finished;
    }

    state def PairOk {
        attribute low : Integer := 0;
        attribute high : Integer := 5;
        assert constraint inOrder { low <= high }
        entry; then idle;
        state idle { entry action { assign high := 20; assign low := 10; } }
        state finished;
        transition first idle then finished;
    }

    // A change notification and a check follow the same write.
    state def Notified {
        attribute hot : Boolean := false;
        attribute count : Integer := 0;
        assert constraint capped { count <= 200 }
        entry; then idle;
        state idle;
        state running;
        transition first idle accept when hot
            do assign count := count + 1 then running;
        transition first running then finished;
        state finished;
    }

    // `limit` is a bound constant: only writes to `count` re-check.
    state def Bound {
        in attribute cap : Integer default 200;
        attribute limit : Integer = 100;
        attribute count : Integer := 0;
        assert constraint within { count <= limit and count <= cap }
        entry; then idle;
        state idle;
        transition first idle accept Go
            do assign count := count + 1 then idle;
    }

    // Expression shapes: a call, `and` and `not`.
    state def Shapes {
        attribute a : Integer := 0;
        attribute b : Integer := 0;
        attribute c : Integer := 0;
        attribute d : Integer := 0;
        assert constraint viaCall { max(a, 3) < 10 }
        assert constraint viaNot { not (a == 5) and c >= 0 }
        entry; then idle;
        state idle;
        transition first idle accept Go
            do action {
                assign a := 1;
                assign c := 1;
                assign d := 1;
            }
            then idle;
    }

    // A write to a field of an item attribute re-checks the constraint.
    state def Fielded {
        item pt : Pt;
        assert constraint onField { pt.x >= 0 }
        entry; then idle;
        state idle;
        transition first idle accept Go
            do assign pt.x := 0 - 1 then idle;
    }
}
