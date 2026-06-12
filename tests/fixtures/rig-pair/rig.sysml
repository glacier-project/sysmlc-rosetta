package RigPair {
    private import ScalarValues::*;
    private import SI::*;

    item def Go;
    item def Done;

    state def Plant {
        port commPort;

        entry; then idle;
        state idle;

        state working {
            entry; then grind;
            state grind;
            transition first grind
                accept after 0.2 [s]
                do send new Done() via commPort
                then done;
        }

        transition first idle accept Go then working;
        transition first working then idle;
    }

    state def PlantTest {
        port commPort;

        attribute verdict : Integer := 0;
        assert constraint testPassed { verdict == 0 }

        entry; then kicking;
        state kicking;
        state waiting;
        state fail {
            entry assign verdict := 1;
        }

        transition first kicking
            accept after 0.1 [s]
            do send new Go() via commPort
            then waiting;
        transition first waiting accept Done then done;
        transition first waiting accept after 1.0 [s] then fail;
        transition first fail then done;
    }

    part def PlantRig {
        exhibit state plant : Plant;
        exhibit state tb : PlantTest;
    }
}
