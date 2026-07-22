package WhenAssignLoop {
    private import SI::*;
    private import ScalarValues::*;

    // Self-driving `accept when` whose EFFECT assigns a watched attribute.
    // The `after` self-loop bumps `n` each re-entry; once `n >= 2` the change
    // trigger fires AND assigns `n`. Proves the change-notify is gated by the
    // assigning branch (a top-level notify would loop forever at frozen time).
    state def Machine {
        attribute n : Integer := 0;
        entry;
            then idle;
        state idle {
            entry assign n := n + 1;
        }
        state running;
        transition first idle accept when n >= 2 do assign n := n + 100 then running;
        transition first idle accept after 1 [s] then idle;
        transition first running then done;
    }
}
