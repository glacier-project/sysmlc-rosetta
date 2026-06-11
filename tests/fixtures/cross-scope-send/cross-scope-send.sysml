package CrossScopeSend {
    private import ScalarValues::*;

    item def Ping;

    // The substate `inner` sends Ping from inside the composite `group`,
    // but Ping is accepted by a group interrupt handled at the root scope:
    // the event would have to cross reactor boundaries.
    state def Machine {
        port commPort;
        entry;
            then group;
        state group {
            entry;
                then inner;
            state inner {
                entry send new Ping() via commPort;
            }
        }
        state other;
        transition first group accept Ping via commPort then other;
    }
}
