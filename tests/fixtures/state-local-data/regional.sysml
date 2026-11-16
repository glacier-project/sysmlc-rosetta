package StateLocalRegions {
    private import ScalarValues::*;
    private import SI::*;

    state def PrivateRegions parallel {
        attribute sequence : Integer := 0;
        attribute visits : Integer := 0;
        state firstRegion {
            attribute temperature : Integer := 10;
            entry; then waiting;
            exit assign sequence := temperature;
            state waiting;
            transition first waiting accept after 0.001 [s]
                do assign temperature := temperature + 1 then done;
        }
        state secondRegion {
            attribute temperature : Integer := 20;
            entry; then waiting;
            exit assign visits := temperature;
            state waiting;
            transition first waiting accept after 0.0015 [s]
                do assign temperature := temperature + 2 then done;
        }
    }

    state def SharedWithinRegion {
        entry; then outer;
        state outer parallel {
            state region {
                attribute temperature : Integer := 10;
                entry; then inner;
                state inner parallel {
                    state left {
                        entry; then waiting;
                        state waiting;
                        transition first waiting accept after 0.001 [s]
                            do assign temperature := temperature + 1 then done;
                    }
                    state right {
                        entry; then waiting;
                        state waiting;
                        transition first waiting accept after 0.001 [s]
                            do assign temperature := temperature + 2 then done;
                    }
                }
            }
        }
    }
}
