// Literal `=` bindings stay readable constants; `:=` values stay assignable.
package Bindings {
    private import ScalarValues::*;
    private import SI::*;
    private import ISQ::*;

    enum def Mode { enum idle; enum busy; }
    enum def Light { enum red = "r"; enum green = "g"; }

    state def Machine {
        attribute k : Integer = 1;
        attribute ratio : Real = -2.5;
        attribute setting : Mode = Mode::busy;
        attribute light : Light = Light::green;
        attribute positive : Integer = +4;
        attribute holdTime : DurationValue = 2 [s];
        attribute limit : Integer default 3;
        attribute a : Integer := 1;
        attribute seen : Integer := 0;
        entry; then idle;
        state idle { entry assign a := 5; }
        state checked { entry assign seen := a + k + limit; }
        state finished;
        state wrong;
        transition first idle
            if a == 5 and k == 1 and setting == Mode::busy
                and light == Light::green then checked;
        transition first idle
            if not (a == 5 and k == 1 and setting == Mode::busy
                and light == Light::green) then wrong;
        transition first checked
            if seen == 9 and ratio < 0.0 and positive == 4 then finished;
        transition first checked
            if not (seen == 9 and ratio < 0.0 and positive == 4) then wrong;
    }

    // `b` must equal a + 1 at every snapshot: after `a := 5` that is 6,
    // which a value computed once at initialization does not maintain.
    state def DynamicBinding {
        attribute a : Integer := 1;
        attribute b : Integer = a + 1;
        entry; then idle;
        state idle { entry assign a := 5; }
        state yes;
        state no;
        transition first idle if b == 6 then yes;
        transition first idle if not (b == 6) then no;
    }

    // `k` is bound to 1: assigning 2 would break the equality.
    state def BoundWrite {
        attribute k : Integer = 1;
        entry; then idle;
        state idle { entry assign k := 2; }
    }
}
