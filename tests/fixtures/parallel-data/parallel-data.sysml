package ParallelData {
    private import ScalarValues::*;
    private import SI::*;

    state def Cleanup {
        attribute sequence : Integer := 0;
        assert constraint bounded { sequence >= 0 }
        entry; then working;
        state working parallel {
            exit assign sequence := sequence * 10 + 5;
            state firstRegion {
                entry; then nested;
                exit assign sequence := sequence * 10 + 2;
                state nested {
                    entry; then active;
                    state active {
                        exit assign sequence := sequence * 10 + 1;
                    }
                }
            }
            state secondRegion {
                entry; then active;
                exit assign sequence := sequence * 10 + 4;
                state active {
                    exit assign sequence := sequence * 10 + 3;
                }
                state unused;
                transition first unused accept after 0.003 [s] then active;
            }
        }
        state finishedState;
        transition first working accept after 0.001 [s] then finishedState;
    }
}
