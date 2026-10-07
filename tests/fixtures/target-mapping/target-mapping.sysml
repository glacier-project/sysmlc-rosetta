package TargetMapping {
    private import ScalarValues::*;
    private import NumericalFunctions::*;
    private import SI::*;
    item def Reset;
    item def Enter;
    item def Cancel;
    item def Advance;
    item def Reading { attribute value : Real; }
    item def Reply { attribute value : Real; }

    state def TimerLoop {
        attribute count : Integer := 0;
        entry; then idle;
        state idle;
        transition first idle accept after 1 [s]
            do assign count := count + 1 then idle;
        transition first idle accept Reset do assign count := 0 then idle;
    }
    state def Counter {
        in attribute limit : Real default 2.0;
        in attribute step : Real default 1.0;
        inout attribute value : Real := 0.0;
        assert constraint nonnegative { value >= 0.0 }
        assert constraint positiveLimit { limit > 0.0 }
        entry; then run;
        state run;
        state complete;
        transition first run accept after 1 [s]
            do assign value := min(value + step, limit) then run;
        transition first run if value >= limit then complete;
    }
    state def PayloadDispatch {
        port commPort;
        attribute limit : Real := 2.0;
        entry; then idle;
        state idle;
        state selected;
        state overflow;
        transition first idle accept reading : Reading
            if reading.value < limit then idle;
        transition first idle accept reading : Reading
            if reading.value == limit
            do send new Reply(reading.value) via commPort then selected;
        transition first idle accept reading : Reading then overflow;
    }
    state def NestedSignals {
        entry; then idle;
        state idle;
        state work {
            entry; then a;
            state a;
            state b;
            transition first a accept Advance then b;
        }
        transition first idle accept Enter then work;
        transition first work accept Cancel then idle;
    }
    state def DeepExit {
        entry; then work;
        state work {
            entry; then workers;
            state workers parallel {
                state monitor {
                    entry; then watching;
                    state watching;
                    transition first watching accept Cancel then stopped;
                }
            }
        }
        state stopped;
    }
    state def MixedJoin {
        entry; then work;
        state work parallel {
            state timed {
                entry; then waiting;
                state waiting;
                transition first waiting accept after 1 [s] then done;
            }
            state signaled {
                entry; then waiting;
                state waiting;
                transition first waiting accept Advance then done;
            }
        }
        state complete;
        transition first work then complete;
    }
    state def GuardOrder {
        attribute ready : Boolean := true;
        entry; then idle;
        state idle;
        state a;
        state b;
        transition first idle if ready then a;
        transition first idle if true then b;
    }
}
