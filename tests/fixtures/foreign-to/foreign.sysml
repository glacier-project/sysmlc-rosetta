package ForeignTo {
    private import ScalarValues::*;

    item def Go;

    part def Controller { port inPort; }
    part controller : Controller;

    // A send addressed to another occurrence is cross-machine addressing,
    // which a single reactor cannot deliver.
    state def MachineSendToPeer {
        entry;
            then idle;
        state idle;
        transition first idle do send new Go() to controller then done;
    }

    // A chained receiver (another machine's port) is cross-machine too.
    state def MachineSendToPeerPort {
        entry;
            then idle;
        state idle;
        transition first idle do send new Go() to controller.inPort then done;
    }
}
