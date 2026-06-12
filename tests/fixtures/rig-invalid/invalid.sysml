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
    part def Twice {
        exhibit state x : A;
        exhibit state y : A;
    }
    part def ExtraMember {
        attribute stray : Integer := 0;
        exhibit state x : A;
        exhibit state y : B;
    }
    part def BothSend {
        exhibit state x : A;
        exhibit state y : B;
    }
}
