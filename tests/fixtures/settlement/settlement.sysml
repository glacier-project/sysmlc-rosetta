package Settlement {
    private import SI::*;
    private import ScalarValues::*;
    private import Time::TimeInstantValue;
    private import ISQ::DurationValue;

    calc def mark { in label : String; return : Integer; }
    calc def ok { in label : String; return : Boolean; }
    calc def seen { in label : String; return : Boolean; }
    calc def note { in label : String; in value : Integer; return : Integer; }

    item def Marked { attribute n : Integer; }
    item def Stop { attribute id : Integer; attribute need : String; }
    item def Go;
    item def Ping;
    item def Bye { attribute n : Integer; }

    state def TimerStop {
        port selfPort;
        attribute n : Integer := 0;
        entry; then working;
        state working {
            exit assign n := mark("working.exit");
            entry; then warmup;
            state warmup {
                exit send new Marked(n = mark("warmup.exit")) to selfPort;
            }
            state running {
                entry send new Marked(n = mark("running.entry")) to selfPort;
            }
            state ready {
                entry send new Marked(n = mark("ready.entry")) to selfPort;
                exit send new Marked(n = mark("ready.exit")) to selfPort;
            }
            transition first warmup
                accept after 0.2 [s]
                do send new Marked(n = mark("timer.effect")) to selfPort
                then running;
            transition first running if ok("running.auto") then ready;
        }
        state stopped { entry assign n := mark("stopped.entry"); }
        transition first working
            accept s : Stop
            if seen(s.need)
            do assign n := note("stop.effect", s.id)
            then stopped;
    }

    state def TimerTie {
        port selfPort;
        attribute n : Integer := 0;
        entry; then working;
        state working {
            entry; then warmup;
            state warmup;
            state running {
                entry send new Marked(n = mark("running.entry")) to selfPort;
            }
            state ready {
                entry send new Marked(n = mark("ready.entry")) to selfPort;
                exit send new Marked(n = mark("ready.exit")) to selfPort;
            }
            transition first warmup accept after 0.2 [s] then running;
            transition first running if ok("running.auto") then ready;
        }
        state stopped { entry assign n := mark("stopped.entry"); }
        transition first working
            accept after 0.2 [s]
            if seen("ready.entry")
            do assign n := mark("timeout.effect")
            then stopped;
    }

    state def AtTie {
        port selfPort;
        attribute n : Integer := 0;
        attribute instant : TimeInstantValue default 0.2 [s];
        entry; then working;
        state working {
            entry; then warmup;
            state warmup;
            state running {
                entry send new Marked(n = mark("running.entry")) to selfPort;
            }
            state ready {
                entry send new Marked(n = mark("ready.entry")) to selfPort;
                exit send new Marked(n = mark("ready.exit")) to selfPort;
            }
            transition first warmup accept after 0.2 [s] then running;
            transition first running if ok("running.auto") then ready;
        }
        state stopped { entry assign n := mark("stopped.entry"); }
        transition first working
            accept at instant
            if seen("ready.entry")
            do assign n := mark("at.effect")
            then stopped;
    }

    state def WhenAtEntry {
        port selfPort;
        attribute n : Integer := 0;
        entry; then working;
        state working {
            entry; then warm;
            state warm;
            state running {
                entry send new Marked(n = mark("running.entry")) to selfPort;
            }
            state ready {
                entry send new Marked(n = mark("ready.entry")) to selfPort;
                exit send new Marked(n = mark("ready.exit")) to selfPort;
            }
            transition first warm if ok("warm.auto") then running;
            transition first running if ok("running.auto") then ready;
        }
        state stopped { entry assign n := mark("stopped.entry"); }
        transition first working
            accept when n == 0
            if seen("ready.entry")
            do assign n := mark("when.effect")
            then stopped;
    }

    state def RegionFinalize {
        attribute n : Integer := 0;
        entry; then working;
        state working parallel {
            state r1 {
                exit assign n := mark("r1.exit");
                entry; then a;
                state a;
                transition first a accept after 0.2 [s] then done;
            }
            state r2 {
                entry; then b;
                state b;
                state c;
                transition first b accept Go then c;
            }
        }
        state stopped { entry assign n := mark("stopped.entry"); }
        transition first working
            accept s : Stop
            if seen(s.need)
            do assign n := note("stop.effect", s.id)
            then stopped;
    }

    state def EscapeOvertake {
        attribute n : Integer := 0;
        entry; then working;
        state working {
            entry; then warmup;
            state warmup;
            state running;
            transition first warmup accept after 0.2 [s] then running;
            transition first running accept Go then escaped;
        }
        state escaped { entry assign n := mark("escaped.entry"); }
        state stopped { entry assign n := mark("stopped.entry"); }
        transition first working
            accept s : Stop
            if seen(s.need)
            do assign n := note("stop.effect", s.id)
            then stopped;
    }

    state def NestedStop {
        port selfPort;
        attribute n : Integer := 0;
        entry; then outer;
        state outer {
            entry; then inner;
            state inner {
                entry; then warmup;
                state warmup;
                state running {
                    entry send new Marked(n = mark("running.entry")) to selfPort;
                }
                transition first warmup accept after 0.2 [s] then running;
            }
            state caught {
                entry send new Marked(n = mark("caught.entry")) to selfPort;
            }
            transition first inner accept s : Stop if seen(s.need) then caught;
        }
        state aborted { entry assign n := mark("aborted.entry"); }
        transition first outer
            accept s : Stop
            do assign n := note("outer.effect", s.id)
            then aborted;
    }

    state def NestedExit {
        port selfPort;
        attribute n : Integer := 0;
        entry; then outer;
        state outer {
            exit assign n := mark("outer.exit");
            entry; then mid;
            state mid {
                exit send new Marked(n = mark("mid.exit")) to selfPort;
                entry; then leaf;
                state leaf {
                    exit send new Marked(n = mark("leaf.exit")) to selfPort;
                }
            }
        }
        state stopped { entry assign n := mark("stopped.entry"); }
        transition first outer
            accept s : Stop
            if seen(s.need)
            do assign n := note("stop.effect", s.id)
            then stopped;
    }

    state def ParallelExit {
        port selfPort;
        attribute n : Integer := 0;
        entry; then working;
        state working parallel {
            exit assign n := mark("working.exit");
            state r1 {
                exit assign n := mark("r1.exit");
                entry; then a;
                state a;
                transition first a accept after 0.1 [s] then done;
            }
            state r2 {
                exit assign n := mark("r2.exit");
                entry; then b0;
                state b0;
                state b {
                    entry send new Ping() to selfPort;
                    exit send new Marked(n = mark("b.exit")) to selfPort;
                }
                state b3;
                transition first b0 accept after 0.2 [s] then b;
                transition first b
                    accept Ping
                    do send new Marked(n = mark("ping.accepted")) to selfPort
                    then b3;
            }
            state r3 {
                exit assign n := mark("r3.exit");
                entry; then c;
                state c {
                    exit send new Marked(n = mark("c.exit")) to selfPort;
                    entry; then d;
                    state d {
                        exit send new Marked(n = mark("d.exit")) to selfPort;
                    }
                }
            }
        }
        state stopped { entry assign n := mark("stopped.entry"); }
        transition first working
            accept s : Stop
            if seen(s.need)
            do assign n := note("stop.effect", s.id)
            then stopped;
    }

    state def DeepEscape {
        port selfPort;
        attribute n : Integer := 0;
        entry; then g;
        state g parallel {
            exit assign n := mark("g.exit");
            state r0 {
                entry; then idle;
                state idle;
            }
            state r1 {
                entry; then s;
                state s {
                    exit send new Marked(n = mark("s.exit")) to selfPort;
                    entry; then p;
                    state p {
                        exit send new Marked(n = mark("p.exit")) to selfPort;
                        entry; then leaf;
                        state leaf {
                            exit send new Marked(n = mark("leaf.exit")) to selfPort;
                        }
                        transition first p
                            accept Stop
                            do send new Marked(n = mark("p.effect")) to selfPort
                            then outside;
                    }
                }
            }
        }
        state outside { entry assign n := mark("outside.entry"); }
        transition first g accept Go then outside;
    }

    state def LatePortClash {
        entry; then g;
        state g {
            entry; then p;
            state p {
                entry; then leaf;
                state leaf;
                transition first p accept Stop then outside;
            }
            state exit_0_done;
        }
        state outside;
    }

    state def SharedStop {
        port selfPort;
        attribute n : Integer := 0;
        entry; then working;
        state working parallel {
            state r1 {
                entry; then a;
                state a;
                state a2;
                transition first a
                    accept s : Stop
                    if seen(s.need)
                    do send new Marked(n = note("r1.effect", s.id)) to selfPort
                    then a2;
            }
            state r2 {
                entry; then b;
                state b;
                state b2;
                transition first b
                    accept s : Stop
                    if ok("r2.guard")
                    do send new Marked(n = note("r2.effect", s.id)) to selfPort
                    then b2;
            }
            state r3 {
                entry; then c;
                state c;
                state c2;
                transition first c
                    accept s : Stop
                    if ok("r3.guard")
                    do send new Marked(n = note("r3.effect", s.id)) to selfPort
                    then c2;
            }
        }
    }

    state def SharedHeld {
        port selfPort;
        attribute n : Integer := 0;
        entry; then working;
        state working parallel {
            state r1 {
                entry; then keep;
                state keep {
                    entry; then q;
                    state q {
                        entry; then warmup;
                        state warmup;
                        state running {
                            entry send new Marked(n = mark("running.entry"))
                                to selfPort;
                        }
                        transition first warmup accept after 0.2 [s] then running;
                    }
                    state q2;
                    transition first q
                        accept st : Stop
                        if seen(st.need)
                        do send new Marked(n = note("r1.effect", st.id))
                            to selfPort
                        then q2;
                }
                state keep2;
                transition first keep
                    accept st : Stop
                    if st.id == 5
                    do send new Marked(n = note("keep.effect", st.id)) to selfPort
                    then keep2;
            }
            state r2 {
                entry; then b;
                state b;
                state b2;
                transition first b
                    accept s : Stop
                    if ok("r2.guard") and s.id < 6
                    do send new Marked(n = note("r2.effect", s.id)) to selfPort
                    then b2;
            }
        }
        state stopped { entry assign n := mark("stopped.entry"); }
        transition first working
            accept s : Stop
            do assign n := note("fallback.effect", s.id)
            then stopped;
    }

    state def SharedHeldEscape {
        port selfPort;
        attribute n : Integer := 0;
        entry; then working;
        state working parallel {
            state r1 {
                entry; then keep;
                state keep {
                    entry; then q;
                    state q {
                        entry; then warmup;
                        state warmup;
                        state running;
                        transition first warmup accept after 0.2 [s] then running;
                        transition first running then caught;
                    }
                    state caught {
                        entry send new Marked(n = mark("caught.entry"))
                            to selfPort;
                    }
                    transition first q
                        accept st : Stop
                        do send new Marked(n = note("q.effect", st.id))
                            to selfPort
                        then caught;
                }
                state keep2;
                transition first keep
                    accept st : Stop
                    do send new Marked(n = note("keep.effect", st.id)) to selfPort
                    then keep2;
            }
            state r2 {
                entry; then b;
                state b;
                state b2;
                transition first b
                    accept s : Stop
                    if ok("r2.guard")
                    do send new Marked(n = note("r2.effect", s.id)) to selfPort
                    then b2;
            }
        }
    }

    state def SharedRoot parallel {
        port selfPort;
        attribute n : Integer := 0;
        state r1 {
            entry; then a;
            state a;
            state a2;
            transition first a
                accept s : Stop
                if seen(s.need)
                do send new Marked(n = note("r1.effect", s.id)) to selfPort
                then a2;
        }
        state r2 {
            entry; then b;
            state b;
            state b2;
            transition first b
                accept s : Stop
                if ok("r2.guard")
                do send new Marked(n = note("r2.effect", s.id)) to selfPort
                then b2;
        }
    }

    state def OriginClash {
        entry; then working;
        state working parallel {
            state r1 {
                entry; then a;
                state a;
                transition first a accept Stop then a;
            }
            state r2 {
                entry; then Stop_origin;
                state Stop_origin;
                transition first Stop_origin accept Stop then Stop_origin;
            }
        }
    }

    state def WaitClash {
        attribute _working_r2_Stop_wait : Integer := 0;
        entry; then working;
        state working parallel {
            state r1 {
                entry; then a;
                state a;
                transition first a accept Stop then a;
            }
            state r2 {
                entry; then b;
                state b;
                transition first b accept Stop then b;
            }
        }
    }

    state def RestartTimer {
        port selfPort;
        attribute n : Integer := 0;
        entry; then working;
        state working {
            entry; then waiting;
            state waiting;
            state fired;
            transition first waiting
                accept after 0.3 [s]
                do send new Marked(n = mark("timer.effect")) to selfPort
                then fired;
        }
        transition first working
            accept Go
            do assign n := mark("restart.effect")
            then working;
    }

    state def ParallelReentry {
        attribute n : Integer := 0;
        entry; then working;
        state working parallel {
            state ra {
                exit assign n := mark("ra.exit");
                entry; then a;
                state a;
                transition first a accept after 0.05 [s] then done;
            }
            state rb {
                exit assign n := mark("rb.exit");
                entry; then b;
                state b;
                transition first b accept after 0.3 [s] then done;
            }
        }
        state away;
        transition first working accept Stop then away;
        transition first away accept Go then working;
    }

    state def SharedLifetime {
        port selfPort;
        attribute n : Integer := 0;
        entry; then working;
        state working parallel {
            state r1 {
                entry; then q;
                state q {
                    entry; then warmup;
                    state warmup;
                    state running {
                        entry send new Marked(n = mark("running.entry"))
                            to selfPort;
                    }
                    transition first warmup accept after 0.2 [s] then running;
                }
                state q2;
                transition first q
                    accept st : Stop
                    if seen(st.need)
                    do send new Marked(n = note("r1.effect", st.id)) to selfPort
                    then q2;
            }
            state r2 {
                entry; then b;
                state b;
                state c {
                    entry send new Marked(n = mark("c.entry")) to selfPort;
                }
                state c2;
                transition first b accept after 0.2 [s] then c;
                transition first c
                    accept st : Stop
                    do send new Marked(n = note("c.effect", st.id)) to selfPort
                    then c2;
            }
        }
    }

    state def SharedReentry {
        port selfPort;
        attribute n : Integer := 0;
        entry; then working;
        state working parallel {
            state r1 {
                entry; then q;
                state q {
                    entry; then warmup;
                    state warmup;
                    state running;
                    transition first warmup accept after 0.2 [s] then running;
                }
                state q2;
                transition first q
                    accept st : Stop
                    if seen(st.need)
                    do send new Marked(n = note("r1.effect", st.id)) to selfPort
                    then q2;
            }
            state r2 {
                entry; then b;
                state b;
                state b2;
                transition first b accept after 0.2 [s] then away;
                transition first b
                    accept st : Stop
                    do send new Marked(n = note("r2.effect", st.id)) to selfPort
                    then b2;
            }
        }
        state away;
        transition first away accept Go then working;
    }

    state def DueClash {
        attribute _t_idle_due : Integer := 0;
        entry; then idle;
        state idle;
        transition first idle accept after 0.1 [s] then idle;
    }

    state def RetimeAt {
        attribute n : Integer := 0;
        attribute late : TimeInstantValue default 0.5 [s];
        attribute early : TimeInstantValue default 0.05 [s];
        attribute instant : TimeInstantValue := 0.3 [s];
        entry; then waiting;
        state waiting;
        state fired { entry assign n := mark("fired.entry"); }
        transition first waiting
            accept at instant
            do assign n := mark("at.effect")
            then fired;
        transition first waiting
            accept Go
            do assign instant := late
            then waiting;
        transition first waiting
            accept Ping
            do assign instant := early
            then waiting;
    }

    state def SameDue {
        attribute n : Integer := 0;
        attribute shorter : DurationValue default 0.2 [s];
        attribute d : DurationValue := 0.3 [s];
        entry; then waiting;
        state waiting;
        state fired;
        transition first waiting accept after d if seen("never") then fired;
        transition first waiting
            accept Go
            do assign d := shorter
            then waiting;
    }

    state def ExitSender {
        port tx;
        attribute n : Integer := 0;
        entry; then outer;
        state outer {
            exit assign n := mark("sender.outer.exit");
            entry; then mid;
            state mid {
                entry; then leaf;
                state leaf {
                    exit send new Bye(n = 7 + mark("sender.leaf.exit")) via tx;
                }
            }
        }
        state stopped { entry assign n := mark("sender.stopped.entry"); }
        transition first outer
            accept after 0.1 [s]
            do assign n := mark("sender.effect")
            then stopped;
    }

    state def ExitWaiter {
        port rx;
        attribute n : Integer := 0;
        entry; then waiting;
        state waiting;
        state got { entry assign n := mark("waiter.got"); }
        transition first waiting
            accept b : Bye via rx
            do assign n := note("waiter.payload", b.n)
            then got;
    }

    part def Sender { port tx; exhibit state : ExitSender; }
    part def Waiter { port rx; exhibit state : ExitWaiter; }

    part peers {
        part sender : Sender;
        part waiter : Waiter;
        connect sender.tx to waiter.rx;
    }
}
