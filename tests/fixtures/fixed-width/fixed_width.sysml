package FixedWidth {
    private import ScalarValues::*;
    private import FixedWidthScalarValues::*;

    attribute percent :> uint8 { assert constraint { that <= 100 } }
    attribute pc :> percent default 1;

    package Other {
        attribute uint8 : Natural;
        attribute fake :> Other::uint8 default 0;
    }

    attribute both1 :> uint8;
    attribute both2 :> int32;
    item def Both { attribute mixed :> both1, both2; }

    item def Reading { attribute raw :> uint16; }

    state def Machine {
        attribute count :> uint8 := 0;
        attribute x :> int32;
        attribute y :> int32;
        attribute z :> int64 := 0;
        attribute big :> uint32 := 0;
        attribute a :> uint16;
        attribute b :> uint16;
        attribute s :> int8 := 0;
        attribute u :> uint8 := 0;
        attribute f :> float32 := 0.5;
        attribute d :> float64 := 0.0;
        attribute r : Real := 0.0;
        attribute p : Integer := 0;
        entry; then idle;
        state idle;
    }

    state def Special :> Machine {
        attribute :>> count;
    }

    state def Wider :> Machine {
        attribute :>> count :> uint16;
    }

    state def Sender {
        port link;
        entry; then idle;
        state idle;
        state sent;
        transition first idle
            do send new Reading(raw = 7) to link
            then sent;
    }
}
