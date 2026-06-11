package Functions {
    private import ScalarValues::*;
    private import NumericalFunctions::*;
    private import TrigFunctions::*;
    private import RealFunctions::*;

    state def Machine {
        attribute x : Real := -2.0;
        entry;
            then idle;
        state idle;
        state running;
        transition first idle if abs(x) > 1.0 and cos(x) < 1.0 then running;
    }

    state def MachineUnsupported {
        attribute x : Real := 4.0;
        entry;
            then idle;
        state idle;
        state running;
        transition first idle if sqrt(x) > 1.0 then running;
    }
}
