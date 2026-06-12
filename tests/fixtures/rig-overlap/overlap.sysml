package RigOverlap {
    private import ScalarValues::*;
    private import SI::*;

    item def Tick;

    state def Pulser {
        port commPort;

        entry; then pulsing;
        state pulsing;
        state waiting;
        state finished;
        transition first pulsing
            accept after 0.1 [s]
            do send new Tick() via commPort
            then waiting;
        transition first waiting accept Tick then finished;
        transition first finished then done;
    }

    state def PulserTest {
        entry; then listening;
        state listening;
        state sawTick;
        transition first listening accept Tick then sawTick;
        transition first sawTick then done;
    }

    part def PulserRig {
        exhibit state plant : Pulser;
        exhibit state tb : PulserTest;
    }
}
