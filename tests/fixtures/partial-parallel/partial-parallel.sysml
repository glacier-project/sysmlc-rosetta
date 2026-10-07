package PartialParallel {
    item def EventA;
    item def EventB;
    state def Machine parallel {
        state a {
            entry; then a1;
            state a1;
            transition first a1 accept EventA then done;
        }
        state b {
            entry; then b1;
            state b1;
            state b2;
            transition first b1 accept EventB then b2;
        }
    }
}
