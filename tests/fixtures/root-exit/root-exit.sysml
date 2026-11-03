package RootExit {
    private import SI::*;
    private import ScalarValues::*;

    // Test-only observer in root_marks.py; logs each evaluation to stderr.
    calc def mark { in label : String; return : Integer; }
    // Deliberately has no backing Python function.
    calc def unbacked { in x : Integer; return : Integer; }

    item def Go;
    item def Bye;

    state def Waiting {
        attribute n : Integer := 0;
        exit assign n := mark("root.exit");
        entry; then idle;
        state idle;
        transition first idle accept Go then done;
    }

    state def Finishing {
        port selfPort;
        attribute n : Integer := 0;
        exit action {
            assign n := mark("root.exit");
            send new Bye() to selfPort;
        }
        entry; then idle;
        state idle { exit assign n := mark("idle.exit"); }
        transition first idle
            accept after 0.1 [s]
            do assign n := mark("effect")
            then done;
    }

    state def ParallelStaggered parallel {
        attribute n : Integer := 0;
        exit assign n := mark("root.exit");
        state r1 {
            exit assign n := mark("r1.exit");
            entry; then a;
            state a;
            transition first a accept after 0.2 [s] then done;
        }
        state r2 {
            exit assign n := mark("r2.exit");
            entry; then b;
            state b;
            transition first b accept after 0.1 [s] then done;
        }
    }

    // Declared out of name order: same-tag region exits follow declaration.
    state def ParallelSimultaneous parallel {
        attribute n : Integer := 0;
        exit assign n := mark("root.exit");
        state r2 {
            exit assign n := mark("r2.exit");
            entry; then b;
            state b;
            transition first b accept after 0.1 [s] then done;
        }
        state r1 {
            exit assign n := mark("r1.exit");
            entry; then a;
            state a;
            transition first a accept after 0.1 [s] then done;
        }
    }

    // A region exit falsifies the guard of the parent's completion.
    state def NestedParallelGuard {
        port selfPort;
        attribute allow : Boolean := true;
        attribute n : Integer := 0;
        entry; then working;
        state working parallel {
            exit assign n := mark("working.exit");
            state motor {
                exit action {
                    assign allow := false;
                    assign n := mark("motor.exit");
                    send new Bye() to selfPort;
                }
                entry; then on;
                state on;
                transition first on accept after 0.1 [s] then done;
            }
            state light {
                exit assign n := mark("light.exit");
                entry; then lit;
                state lit;
                transition first lit accept after 0.2 [s] then done;
            }
        }
        state dest { entry assign n := mark("dest.entry"); }
        transition first working
            if allow
            do assign n := mark("effect")
            then dest;
    }

    state def BadExitNoDone {
        attribute n : Integer := 0;
        exit assign n := unbacked(1);
        entry; then idle;
        state idle;
    }

    state def BadExitParallelNoDone parallel {
        attribute n : Integer := 0;
        exit assign n := unbacked(1);
        state r1 {
            entry; then a;
            state a;
        }
    }

    state def BadRegionExitNoDone parallel {
        attribute n : Integer := 0;
        state r1 {
            exit assign n := unbacked(1);
            entry; then a;
            state a;
        }
    }

    state def FinisherBehavior parallel {
        port tx;
        attribute n : Integer := 0;
        exit assign n := mark("finisher.exit");
        state work {
            exit action {
                assign n := mark("finisher.work.exit");
                send new Bye() via tx;
            }
            entry; then idle;
            state idle;
            transition first idle accept after 0.1 [s] then done;
        }
    }

    state def WaiterBehavior {
        port rx;
        attribute n : Integer := 0;
        exit assign n := mark("waiter.exit");
        entry; then waiting;
        state waiting { exit assign n := mark("waiter.waiting.exit"); }
        state got;
        transition first waiting accept Bye via rx then got;
    }

    part def Finisher { port tx; exhibit state : FinisherBehavior; }
    part def Waiter { port rx; exhibit state : WaiterBehavior; }

    part peers {
        part finisher : Finisher;
        part waiter : Waiter;
        connect finisher.tx to waiter.rx;
    }
}
