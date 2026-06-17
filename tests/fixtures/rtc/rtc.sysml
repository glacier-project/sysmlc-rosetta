package Rtc {
    private import SI::*;
    private import ScalarValues::*;

    item def A;
    item def B;

    // Both transitions route to `chooseResult` and write `chosen`; the chooseResult
    // branch makes WHICH effect ran observable in current_state.  So the
    // terminal state proves both "right reset won" and "loser effect did
    // NOT run" (double-execution would set chosen := 2).
    state def TwoSignals {
        attribute chosen : Integer := 0;
        entry;
            then idle;
        state idle;
        state chooseResult;
        state aResult;
        state bResult;
        transition first idle
            accept A
            do assign chosen := 1
            then chooseResult;
        transition first idle
            accept B
            do assign chosen := 2
            then chooseResult;
        transition first chooseResult
            if chosen == 1
            then aResult;
        transition first chooseResult
            if chosen == 2
            then bResult;
    }

    // Signal declared FIRST -> signal wins a same-tag collision with after.
    state def SignalThenAfter {
        attribute chosen : Integer := 0;
        entry;
            then idle;
        state idle;
        state chooseResult;
        state sigWon;
        state timeoutWon;
        transition first idle
            accept A
            do assign chosen := 1
            then chooseResult;
        transition first idle
            accept after 1 [s]
            do assign chosen := 2
            then chooseResult;
        transition first chooseResult
            if chosen == 1
            then sigWon;
        transition first chooseResult
            if chosen == 2
            then timeoutWon;
    }

    // After declared FIRST -> timeout wins (declaration order honored
    // uniformly across signal vs after).
    state def AfterThenSignal {
        attribute chosen : Integer := 0;
        entry;
            then idle;
        state idle;
        state chooseResult;
        state sigWon;
        state timeoutWon;
        transition first idle
            accept after 1 [s]
            do assign chosen := 2
            then chooseResult;
        transition first idle
            accept A
            do assign chosen := 1
            then chooseResult;
        transition first chooseResult
            if chosen == 2
            then timeoutWon;
        transition first chooseResult
            if chosen == 1
            then sigWon;
    }

    // Three-way: A, B, after; A declared first wins.
    state def ThreeWay {
        attribute chosen : Integer := 0;
        entry;
            then idle;
        state idle;
        state chooseResult;
        state ra;
        state rb;
        state rt;
        transition first idle
            accept A
            do assign chosen := 1
            then chooseResult;
        transition first idle
            accept B
            do assign chosen := 2
            then chooseResult;
        transition first idle
            accept after 1 [s]
            do assign chosen := 3
            then chooseResult;
        transition first chooseResult
            if chosen == 1
            then ra;
        transition first chooseResult
            if chosen == 2
            then rb;
        transition first chooseResult
            if chosen == 3
            then rt;
    }

    // Guard-false higher priority: A is declared first but its guard is
    // false, so B wins (both backends agree).
    state def GuardFalse {
        attribute chosen : Integer := 0;
        attribute allowA : Boolean := false;
        entry;
            then idle;
        state idle;
        state chooseResult;
        state ra;
        state rb;
        transition first idle
            accept A
            if allowA
            do assign chosen := 1
            then chooseResult;
        transition first idle
            accept B
            do assign chosen := 2
            then chooseResult;
        transition first chooseResult
            if chosen == 1
            then ra;
        transition first chooseResult
            if chosen == 2
            then rb;
    }

    // Single-trigger control: must NOT gain a _fired flag.
    state def SingleTrigger {
        entry;
            then idle;
        state idle;
        state done2;
        transition first idle
            accept A
            then done2;
    }

    // A sibling literally named `idle_fired` collides with the flag for the
    // multi-trigger state `idle`.
    state def NameClashFired {
        entry;
            then idle;
        state idle;
        state idle_fired;
        state other;
        transition first idle
            accept A
            then idle_fired;
        transition first idle
            accept B
            then other;
    }
}
