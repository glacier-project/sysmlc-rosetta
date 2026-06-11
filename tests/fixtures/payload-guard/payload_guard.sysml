package PayloadGuard {
    private import ScalarValues::*;

    item def Reading {
        attribute value : Integer;
    }

    // The guard reads the accept payload: rosetta must fail loud, not
    // generate Python that references an undefined name.
    state def Machine {
        entry;
            then idle;
        state idle;
        state running;
        transition first idle
            accept r : Reading
            if r.value > 0
            then running;
    }

    // WRITING to the accept payload (RPC-style out parameter) is not
    // supported: payload locals are read-only in rosetta.
    state def MachineWriteBack {
        entry;
            then idle;
        state idle;
        state running;
        transition first idle
            accept r : Reading
            do assign r.value := 0
            then running;
    }
}
