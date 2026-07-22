// A self-driving `accept when`: an `after` self-loop bumps `n` on every
// re-entry of `idle`; the change trigger fires once `n` reaches 3.
package WhenCounter {
    private import SI::*;
    private import ScalarValues::*;

    state def Machine {
        attribute n : Integer := 0;
        entry;
            then idle;
        state idle {
            entry assign n := n + 1;
        }
        state running;
        transition first idle accept when n >= 3 then running;
        transition first idle accept after 1 [s] then idle;
        transition first running then done;
    }
}
