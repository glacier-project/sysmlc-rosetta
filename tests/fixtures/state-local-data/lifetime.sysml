package StateLocalData {
    private import ScalarValues::*;
    private import SI::*;

    attribute def Box { attribute count : Integer default 1; }

    state def Lifetime {
        attribute temperature : Integer := 100;
        attribute sequence : Integer := 0;
        attribute visits : Integer := 0;
        attribute enabled : Boolean := false;
        attribute replacement : Box { attribute :>> count default 2; }
        entry; then enclosing;
        state enclosing {
            attribute temperature : Integer := 41;
            attribute box : Box;
            entry action {
                assign enabled := box.count == 1;
                assign temperature := temperature + 1;
            }
            then firstChild;
            exit action {
                assign sequence := sequence + temperature;
                assign enabled := enabled and box.count == 2;
            }
            state firstChild {
                entry action {
                    assign temperature := temperature + 1;
                    assign box := replacement;
                    assign sequence := sequence + temperature;
                }
            }
            state secondChild {
                entry assign sequence := sequence + temperature;
            }
            transition first firstChild accept after 0.001 [s]
                do assign temperature := temperature + 1 then secondChild;
        }
        state finishedState { entry assign visits := visits + 1; }
        transition first enclosing accept after 0.002 [s] then finishedState;
        transition first finishedState accept after 0.001 [s]
            if visits < 2 then enclosing;
    }
}
