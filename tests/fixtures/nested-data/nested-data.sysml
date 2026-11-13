package NestedData {
    private import ScalarValues::*;
    private import SI::*;

    state def Completion {
        in attribute digit : Integer default 1;
        attribute sequence : Integer := 0;
        attribute visits : Integer := 0;
        attribute enabled : Boolean := false;
        attribute temperature : Real := 20.5;
        entry assign temperature := 21.0;
        then enclosing;
        state enclosing {
            entry assign enabled := true;
            then child;
            exit assign sequence := sequence * 10 + 2;
            state child {
                entry assign temperature := temperature + 0.5;
                exit action {
                    assign sequence := sequence * 10 + digit;
                    assign enabled := false;
                }
            }
            transition first child accept after 0.002 [s]
                if enabled then done;
        }
        state finishedState {
            entry action {
                assign sequence := sequence * 10 + 4;
                assign visits := visits + 1;
            }
        }
        transition first enclosing if not enabled
            do assign sequence := sequence * 10 + 3 then finishedState;
        transition first finishedState accept after 0.001 [s]
            if visits < 2 then enclosing;
    }

    state def ParentChange {
        attribute enabled : Boolean := false;
        attribute sequence : Integer := 0;
        entry; then enclosing;
        state enclosing {
            entry; then nested;
            exit assign sequence := sequence * 10 + 2;
            state nested {
                entry; then child;
                state child {
                    exit action {
                        assign sequence := sequence * 10 + 1;
                        assign enabled := true;
                    }
                }
                transition first child accept after 0.002 [s] then done;
            }
            state ready;
            transition first nested then ready;
        }
        state finishedState {
            entry assign sequence := sequence * 10 + 4;
        }
        transition first enclosing accept when enabled
            do assign sequence := sequence * 10 + 3 then finishedState;
    }

    state def TimedParent {
        attribute enabled : Boolean := false;
        attribute sequence : Integer := 0;
        entry; then enclosing;
        state enclosing {
            entry; then mid;
            exit assign sequence := sequence * 10 + 4;
            state mid {
                entry; then child;
                exit assign sequence := sequence * 10 + 3;
                state child {
                    exit assign sequence := sequence * 10 + 1;
                }
                state ready {
                    entry assign enabled := true;
                    exit assign sequence := sequence * 10 + 2;
                }
                transition first child accept after 0.002 [s] then ready;
            }
        }
        state finishedState {
            entry assign sequence := sequence * 10 + 6;
        }
        transition first enclosing accept after 0.002 [s]
            if enabled do assign sequence := sequence * 10 + 5 then finishedState;
    }

    state def Constraint {
        attribute level : Integer := 0;
        assert constraint nonnegative { level >= 0 }
        entry; then enclosing;
        state enclosing {
            entry; then child;
            state child {
                entry assign level := -1;
            }
        }
    }

    state def ParallelAccess {
        attribute level : Integer := 0;
        entry; then enclosing;
        state enclosing parallel {
            state region {
                entry; then child;
                state child { entry assign level := 1; }
            }
        }
    }
}
