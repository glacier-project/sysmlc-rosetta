// Referenced phase actions are valid SysML but outside the compiler subset.
package PhaseActions {
    private import ScalarValues::*;
    action def Work;
    item def Go;
    state def EntryReference {
        attribute count : Integer := 0;
        action helper { assign count := 5; }
        entry; then idle;
        state idle { entry helper; }
        state finished;
    }
    state def EntrySubsetting {
        attribute count : Integer := 0;
        action helper { assign count := 5; }
        entry; then idle;
        state idle { entry action local :> helper; }
        state finished;
    }
    state def EntryTyped {
        attribute count : Integer := 0;
        action helper { assign count := 5; }
        entry; then idle;
        state idle { entry action local : Work; }
        state finished;
    }
    state def EntryTypedLocal {
        attribute count : Integer := 0;
        action helper { assign count := 5; }
        entry; then idle;
        state idle { entry action local : Work { assign count := 9; } }
        state finished;
    }
    state def ExitReference {
        attribute count : Integer := 0;
        action helper { assign count := 5; }
        entry; then idle;
        state idle { exit helper; }
        state finished;
    }
    state def ExitSubsetting {
        attribute count : Integer := 0;
        action helper { assign count := 5; }
        entry; then idle;
        state idle { exit action local :> helper; }
        state finished;
    }
    state def ExitTyped {
        attribute count : Integer := 0;
        action helper { assign count := 5; }
        entry; then idle;
        state idle { exit action local : Work; }
        state finished;
    }
    state def ExitTypedLocal {
        attribute count : Integer := 0;
        action helper { assign count := 5; }
        entry; then idle;
        state idle { exit action local : Work { assign count := 9; } }
        state finished;
    }
    state def EffectReference {
        attribute count : Integer := 0;
        action helper { assign count := 5; }
        entry; then idle;
        state idle {  }
        state finished;
        transition first idle accept Go do helper then finished;
    }
    state def EffectSubsetting {
        attribute count : Integer := 0;
        action helper { assign count := 5; }
        entry; then idle;
        state idle {  }
        state finished;
        transition first idle accept Go do action local :> helper then finished;
    }
    state def EffectTyped {
        attribute count : Integer := 0;
        action helper { assign count := 5; }
        entry; then idle;
        state idle {  }
        state finished;
        transition first idle accept Go do action local : Work then finished;
    }
    state def EffectTypedLocal {
        attribute count : Integer := 0;
        action helper { assign count := 5; }
        entry; then idle;
        state idle {  }
        state finished;
        transition first idle accept Go do action local : Work { assign count := 9; } then finished;
    }
    state def DoReference {
        attribute count : Integer := 0;
        action helper { assign count := 5; }
        entry; then idle;
        state idle { do helper; }
        state finished;
    }
    state def DoSubsetting {
        attribute count : Integer := 0;
        action helper { assign count := 5; }
        entry; then idle;
        state idle { do action local :> helper; }
        state finished;
    }
    state def DoTyped {
        attribute count : Integer := 0;
        action helper { assign count := 5; }
        entry; then idle;
        state idle { do action local : Work; }
        state finished;
    }
    state def DoTypedLocal {
        attribute count : Integer := 0;
        action helper { assign count := 5; }
        entry; then idle;
        state idle { do action local : Work { assign count := 9; } }
        state finished;
    }
    state def EntryReferenceLocal {
        attribute count : Integer := 0;
        action helper { assign count := 5; }
        entry; then idle;
        state idle { entry helper { assign count := 9; } }
        state finished;
    }
    state def ExitReferenceLocal {
        attribute count : Integer := 0;
        action helper { assign count := 5; }
        entry; then idle;
        state idle { exit helper { assign count := 9; } }
        state finished;
    }
    state def EffectReferenceLocal {
        attribute count : Integer := 0;
        action helper { assign count := 5; }
        entry; then idle;
        state idle {  }
        state finished;
        transition first idle accept Go do helper { assign count := 9; } then finished;
    }
    state def DoReferenceLocal {
        attribute count : Integer := 0;
        action helper { assign count := 5; }
        entry; then idle;
        state idle { do helper { assign count := 9; } }
        state finished;
    }
}
