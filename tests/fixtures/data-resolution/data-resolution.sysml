package DataResolution {
    private import ScalarValues::*;
    private import SI::*;

    package Config { attribute count : Integer = 77; }

    state def Qualified {
        attribute count : Integer := 3;
        attribute observed : Integer := 0;
        entry; then enclosing;
        state enclosing {
            entry; then child;
            state child { entry assign observed := Config::count; }
        }
    }

    state def Local {
        attribute observedValue : Integer := 0;
        entry; then enclosing;
        state enclosing {
            attribute count : Integer := 41;
            entry; then child;
            state child { entry assign observedValue := count + 1; }
        }
    }

    state def Flat {
        entry; then _previous_current_state;
        state _previous_current_state;
    }

    state def Intermediate {
        attribute low : Integer := 0;
        attribute high : Integer := 5;
        assert constraint inOrder { low <= high }
        entry; then enclosing;
        state enclosing {
            entry; then child;
            state child {
                entry action { assign low := 10; assign high := 20; }
            }
        }
    }

    state def NamedParallel {
        attribute observedValue : Integer := 0;
        entry; then 'outer.state';
        state 'outer.state' parallel {
            state 'first.region' {
                entry; then 'first.child';
                state 'first.child' {
                    entry assign observedValue := observedValue * 10 + 1;
                }
            }
            state 'second.region' {
                entry; then 'second.child';
                state 'second.child' {
                    entry assign observedValue := observedValue * 10 + 2;
                }
            }
        }
        state finishedState;
        transition first 'outer.state' accept after 0.001 [s]
            then finishedState;
    }
}
