package RigInvalid {
    private import ScalarValues::*;
    private import SI::*;

    item def Ping;

    state def A {
        port commPort;

        entry; then sending;
        state sending;
        transition first sending
            accept after 0.1 [s] do send new Ping() via commPort then done;
        transition first sending accept Ping then done;
    }
    state def B {
        port commPort;

        entry; then sending;
        state sending;
        transition first sending
            accept after 0.1 [s] do send new Ping() via commPort then done;
        transition first sending accept Ping then done;
    }

    part def ThreeExhibits {
        exhibit state x : A;
        exhibit state y : B;
        exhibit state z : A;
    }
    // Shape-valid for exhibited_state_defs (two named exhibits, each typed
    // by a model state def), but rejected by build_composition: the same
    // state def A is exhibited twice, which the composition builder forbids.
    part def Twice {
        exhibit state x : A;
        exhibit state y : A;
    }
    part def ExtraMember {
        attribute stray : Integer := 0;
        exhibit state x : A;
        exhibit state y : B;
    }
    // Shape-valid for exhibited_state_defs (two distinct state defs, two
    // named exhibits), but rejected by build_composition: both A and B
    // send AND accept Ping, so both sides have the same signal name on
    // their commPort — the composition builder cannot assign a unique
    // input port to either machine.
    part def BothSend {
        exhibit state x : A;
        exhibit state y : B;
    }
}
