package Rtc {
    private import SI::*;
    private import ScalarValues::*;

    item def A;
    item def B;
    item def Z { attribute value : Integer; }
    item def ASub :> Z;
    item def Marked { attribute n : Integer; }
    calc def mark { in label : String; return : Integer; }
    calc def note { in label : String; in value : Integer; return : Integer; }

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

    item def Ev;

    // `outer` has BOTH a group interrupt on Ev (1A: inner `inner -> innerB`
    // wins) AND a distinct after timeout.  At a tag where only Ev fires and
    // the inner consumes it, the outer interrupt is suppressed (1A); the
    // after path is unaffected.  Confirms the _fired guard composes with the
    // _consumed guard without breaking inner-first.
    state def Compose1A1B {
        entry; then outer;
        state outer {
            entry; then inner;
            state inner; state innerB;
            transition first inner accept Ev then innerB;
        }
        state aborted;
        state timedOut;
        transition first outer accept Ev then aborted;
        transition first outer accept after 5 [s] then timedOut;
    }

    state def SignalInterleaving {
        entry; then source;
        state source;
        state firstA;
        state selectedB;
        state lastA;
        transition first source accept A if false then firstA;
        transition first source accept B if true then selectedB;
        transition first source accept A if true then lastA;
    }

    state def TimerInterleaving {
        entry; then source;
        state source;
        state firstA;
        state selectedTimer;
        state lastA;
        transition first source accept A if false then firstA;
        transition first source accept after 0.1 [s] if true then selectedTimer;
        transition first source accept A if true then lastA;
    }

    state def LexicalInterleaving {
        entry; then outer;
        state outer {
            entry; then source;
            state source;
            state firstA;
            state selectedB;
            state lastA;
            transition first source accept A if false then firstA;
            transition first source accept B if true then selectedB;
        }
        transition first outer.source accept A if true then outer.lastA;
    }

    state def BusyInterleaving {
        port selfPort;
        attribute n : Integer := 0;
        entry; then source;
        state source {
            exit assign n := mark("source.exit");
            entry; then warmup;
            state warmup {
                exit send new Marked(n = mark("warmup.exit")) to selfPort;
            }
            state ready {
                entry send new Marked(n = mark("ready.entry")) to selfPort;
                exit send new Marked(n = mark("ready.exit")) to selfPort;
            }
            state wrong;
            transition first warmup accept after 0.1 [s] then ready;
            transition first warmup accept ASub if false then wrong;
        }
        state firstA;
        state selectedB;
        state lastA { entry assign n := mark("lastA.entry"); }
        transition first source accept z : Z if false then firstA;
        transition first source accept B if true then selectedB;
        transition first source accept a : ASub if true
            do assign n := note("selected.value", a.value) then lastA;
    }
}
