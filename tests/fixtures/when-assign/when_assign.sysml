package WhenAssign {
    private import ScalarValues::*;

    state def Machine {
        attribute hot : Boolean := false;
        attribute count : Integer := 0;
        entry;
            then idle;
        state idle;
        state running;
        transition first idle accept when hot do assign count := count + 1 then running;
        transition first running then done;
    }
}
