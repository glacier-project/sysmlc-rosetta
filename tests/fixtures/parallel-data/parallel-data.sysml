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
    state def Entry {
        attribute sequence : Integer := 0;
        attribute visits : Integer := 0;
        assert constraint bounded { sequence >= 0 }
        entry assign sequence := 9;
        then working;
        state working parallel {
            state firstRegion {
                entry assign sequence := sequence * 2 + 5;
                then nested;
                state nested parallel {
                    state nestedFirst {
                        entry; then active;
                        state active {
                            entry assign sequence := sequence * 2 + 1;
                        }
                    }
                    state nestedSecond {
                        entry; then active;
                        state active {
                            entry assign sequence := sequence * 2 + 2;
                        }
                    }
                }
                state other;
                transition first nested accept after 0.0005 [s] then other;
            }
            state secondRegion {
                entry assign sequence := sequence * 2 + 6;
                then active;
                state active { entry assign sequence := sequence * 2 + 3; }
            }
            state thirdRegion {
                entry assign sequence := sequence * 2 + 7;
                then active;
                state active { do assign sequence := sequence * 2 + 4; }
            }
        }
        state finishedState { entry assign visits := visits + 1; }
        transition first working accept after 0.001 [s] then finishedState;
        transition first finishedState accept after 0.001 [s]
            if visits < 2 then working;
    }

    state def RootEntry parallel {
        attribute sequence : Integer := 0;
        attribute visits : Integer := 0;
        state firstRegion {
            entry; then active;
            state active {
                entry action {
                    assign sequence := sequence * 10 + 3;
                    assign visits := visits + 1;
                }
            }
        }
        state secondRegion {
            entry; then active;
            state active {
                entry action {
                    assign sequence := sequence * 10 + 4;
                    assign visits := visits + 1;
                }
            }
        }
    }
}
