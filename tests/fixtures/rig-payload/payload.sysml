package RigPayload {
    private import ScalarValues::*;
    private import SI::*;

    item def Report {
        attribute n : Integer;
    }

    state def Counter {
        port commPort;
        entry; then ticking;
        state ticking;
        state waiting;
        state finished;
        transition first ticking
            accept after 0.1 [s]
            do send new Report(7) via commPort
            then waiting;
        // Local accept: makes Report an OVERLAP signal (port + self-event).
        transition first waiting accept Report then finished;
        transition first finished then done;
    }

    state def CounterTest {
        entry; then listening;
        state listening;
        state got;
        transition first listening accept Report then got;
        transition first got then done;
    }

    part def CounterRig {
        exhibit state plant : Counter;
        exhibit state tb : CounterTest;
    }
}
