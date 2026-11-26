package CompositeStateLocal {
    private import ScalarValues::*;

    state def Machine {
        attribute observedValue : Integer := 0;
        entry; then enclosing;
        state enclosing {
            attribute localValue : Integer := 41;
            entry; then child;
            state child {
                entry assign observedValue := localValue + 1;
            }
        }
    }
}
