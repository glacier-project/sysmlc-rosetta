package FixedWidthC {
    private import ScalarValues::*;
    private import FixedWidthScalarValues::*;
    private import NumericalFunctions::*;

    // One effect per generated-C case (tests/test_fixed_width_c.py).
    state def Casts {
        attribute count :> uint8 := 0;
        attribute x :> int32 := 0;
        attribute y :> int32 := 0;
        attribute z :> int64 := 0;
        attribute big :> uint32 := 0;
        attribute a :> uint16 := 0;
        attribute b :> uint16 := 0;
        attribute s32 :> int32 := 0;
        attribute u32 :> uint32 := 0;
        attribute u64 :> uint64 := 0;
        attribute l :> int64 := 0;
        attribute m :> int64 := 0;
        attribute f :> float32 := 0.5;
        attribute d :> float64 := 0.0;
        attribute r : Real := 0.0;
        attribute p : Integer := 0;
        attribute flag : Boolean := false;
        entry; then idle;
        state idle;
        state finished;
        transition first idle
            do action {
                assign z := x * y;
                assign big := a * b;
                assign count := count + 1;
                assign z := max(x * y, 0);
                assign flag := s32 < u32;
                assign flag := u64 > s32;
                assign u64 := 18446744073709551615;
                assign l := 9223372036854775807;
                assign l := -9223372036854775808;
                assign l := x * -2;
                assign l := max(l, m);
                assign m := min(l, 3);
                assign l := abs(m);
                assign l := max(u32, s32);
                assign u64 := min(u64, s32);
                assign r := max(r, 0.5);
                assign p := max(p, 1);
                assign f := f + 0.25;
                assign flag := f < 0.5;
                assign d := d + 0.25;
                assign r := r + 0.75;
            }
            then finished;
    }

    // A widened and a single-precision comparison outside stores.
    state def Conditions {
        attribute s32 :> int32 := -1;
        attribute u32 :> uint32 := 1;
        attribute f :> float32 := 0.25;
        assert constraint ordered { s32 < u32 }
        entry; then idle;
        state idle;
        state finished;
        transition first idle if s32 < u32 and f < 0.5 then finished;
    }

    // Compiled and run natively (tests/lf/test_fixed_width_c.py); every
    // value printed is exact in C and in single precision.
    state def Run {
        attribute x :> int32 := 2147483647;
        attribute y :> int32 := 2147483647;
        attribute product :> int64 := 0;
        attribute s :> int32 := -1;
        attribute u :> uint32 := 1;
        attribute less : Boolean := false;
        attribute top :> uint64 := 0;
        attribute l :> int64 := 0;
        attribute largest :> int64 := 0;
        attribute smallest :> int32 := 0;
        attribute magnitude :> int32 := 0;
        attribute mixed :> int64 := 0;
        attribute low :> int64 := 0;
        attribute doubled :> int64 := 0;
        attribute f :> float32 := 0.5;
        entry; then idle;
        state idle;
        state finished;
        transition first idle
            do action {
                assign product := x * y;
                assign less := s < u;
                assign top := 18446744073709551615;
                assign largest := max(9007199254740993, l);
                assign smallest := min(s, x);
                assign magnitude := abs(s);
                assign mixed := max(u, s);
                assign low := -9223372036854775808;
                assign doubled := x * -2;
                assign f := f + 0.25;
            }
            then finished;
    }
}
