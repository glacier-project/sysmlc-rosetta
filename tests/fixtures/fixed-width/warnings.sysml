package Warnings {
    private import ScalarValues::*;
    private import FixedWidthScalarValues::*;

    // One state def per width-warning case (tests/test_width_warnings.py).

    item def Sample { attribute raw :> uint16; }

    state def Increment {
        attribute count :> uint8 := 0;
        entry; then idle;
        state idle;
        state finished;
        transition first idle do assign count := count + 1 then finished;
    }

    state def Bounded {
        attribute count :> uint8 := 0;
        assert constraint { count <= 200 }
        entry; then idle;
        state idle;
        state finished;
        transition first idle do assign count := count + 1 then finished;
    }

    state def BoundedRange {
        attribute count :> uint8 := 10;
        assert constraint { 10 <= count and count < 101 }
        entry; then idle;
        state idle;
        state stepped;
        state finished;
        transition first idle do assign count := count + 155 then stepped;
        transition first stepped do assign count := count - 11 then finished;
    }

    state def BoundedByAttribute {
        attribute count :> uint8 := 0;
        attribute maxCount : Integer := 200;
        assert constraint { count <= maxCount }
        entry; then idle;
        state idle;
        state finished;
        transition first idle do assign count := count + 1 then finished;
    }

    state def NegatedBound {
        attribute count :> uint8 := 0;
        assert not constraint { count > 200 }
        entry; then idle;
        state idle;
        state finished;
        transition first idle do assign count := count + 1 then finished;
    }

    state def NarrowProductGuard {
        attribute a :> uint16;
        attribute b :> uint16;
        entry; then idle;
        state idle;
        state finished;
        transition first idle if a * b > 100 then finished;
    }

    state def WideningStore {
        attribute x :> int32;
        attribute y :> int32;
        attribute z :> int64 := 0;
        entry; then idle;
        state idle;
        state finished;
        transition first idle do assign z := x * y then finished;
    }

    state def WideProductGuard {
        attribute x :> int32;
        attribute y :> int32;
        entry; then idle;
        state idle;
        state finished;
        transition first idle if x * y > 0 then finished;
    }

    state def PlainIntegerStore {
        attribute count :> uint8 := 0;
        attribute p : Integer := 0;
        entry; then idle;
        state idle;
        state finished;
        transition first idle do assign count := p then finished;
    }

    state def SignedIntoUnsigned {
        attribute s :> int8 := 0;
        attribute u :> uint8 := 0;
        entry; then idle;
        state idle;
        state finished;
        transition first idle do assign u := s then finished;
    }

    state def DoubleIntoFloat {
        attribute f :> float32 := 0.5;
        attribute d :> float64 := 0.0;
        entry; then idle;
        state idle;
        state finished;
        transition first idle do assign f := d then finished;
    }

    state def FloatStores {
        attribute f :> float32 := 0.5;
        entry; then idle;
        state idle;
        state stepped;
        state finished;
        transition first idle do assign f := f + 0.25 then stepped;
        transition first stepped do assign f := 0.1 then finished;
    }

    state def RealIntoInteger {
        attribute count :> uint8 := 0;
        attribute r : Real := 0.0;
        entry; then idle;
        state idle;
        state finished;
        transition first idle do assign count := r then finished;
    }

    // Analysed directly: rosetta's code generators do not render `%` yet.
    state def Wrapping {
        attribute count :> uint8 := 0;
        entry; then idle;
        state idle;
        state finished;
        transition first idle do assign count := (count + 1) % 256 then finished;
    }

    state def PayloadStore {
        port link;
        attribute a :> uint16;
        attribute b :> uint16;
        entry; then idle;
        state idle;
        state finished;
        transition first idle do send new Sample(a + b) to link then finished;
    }

    state def Configurable {
        attribute count :> uint8 := 0;
        entry; then idle;
        state idle;
    }

    state def InDefault {
        in attribute limit :> uint8 default 300;
        entry; then idle;
        state idle;
    }

    state def OutOfRangeInitial {
        attribute count :> uint8 := 256;
        entry; then idle;
        state idle;
    }

    state def ConstraintArithmetic {
        attribute a :> uint16;
        attribute b :> uint16;
        assert constraint { a * b >= 0 }
        entry; then idle;
        state idle;
    }

    state def WhenArithmetic {
        attribute a :> uint16;
        attribute b :> uint16;
        entry; then idle;
        state idle;
        state finished;
        transition first idle accept when a * b > 100 then finished;
    }

    // Analysed directly through WidthAnalysis.check (one value each).
    state def Typing {
        attribute c :> int32 := 0;
        attribute u :> uint32 := 0;
        attribute i :> int32 := 0;
        attribute l :> int64 := 0;
        attribute x :> int32 := 0;
        attribute y :> int32 := 0;
        attribute z :> int64 := 0;
        attribute u64 :> uint64 := 0;
        attribute f :> float32 := 0.5;
        attribute d :> float64 := 0.0;
        attribute r : Real := 0.0;
        attribute flag : Boolean := false;
        entry; then idle;
        state idle;
        state finished;
        transition first idle do assign c := c + 1 then finished;
        transition first idle do assign i := u - 2147483648 then finished;
        transition first idle do assign i := l then finished;
        transition first idle do assign z := x * y then finished;
        transition first idle
            do assign z := NumericalFunctions::max(x * y, 0) then finished;
        transition first idle do assign flag := x < u then finished;
        transition first idle do assign flag := u64 > x then finished;
        transition first idle do assign flag := u64 > 0 then finished;
        transition first idle do assign f := f * 2.0 + 0.25 then finished;
        transition first idle do assign f := 0.1 then finished;
        transition first idle do assign f := r + 0.5 then finished;
        transition first idle do assign d := d + 0.5 then finished;
        transition first idle do assign flag := f < 0.75 then finished;
        transition first idle do assign flag := f == 0.1 then finished;
        transition first idle do assign flag := d < 0.75 then finished;
        transition first idle do assign flag := r == 0.1 then finished;
        transition first idle do assign flag := x * y * l > 0 then finished;
    }
}
