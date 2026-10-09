package ConstraintIdentity {
    private import ScalarValues::*;
    item def Tick;

    state def DuplicateConditions {
        attribute level : Real := 0.0;
        assert constraint firstLimit { level > 0.0 }
        assert constraint secondLimit { level > 0.0 }
        entry; then idle;
        state idle;
    }

    state def ScopedNames {
        attribute level : Real := 0.0;
        entry; then idle;
        state idle { assert constraint limit { level > 0.0 } }
        state running { assert constraint limit { level > 0.0 } }
    }

    state def Anonymous {
        attribute level : Real := 0.0;
        assert constraint { level > 0.0 }
        assert constraint constraint0 { level > 0.0 }
        entry; then idle;
        state idle;
    }

    state def Delayed {
        attribute level : Real := 1.0;
        assert constraint 'limit "quoted"' { level > 0.0 }
        entry; then idle;
        state idle;
        transition first idle accept Tick
            do assign level := 0.0 then idle;
    }

    part def Controller { exhibit state control : DuplicateConditions; }
    part system { part controller : Controller; }
}
