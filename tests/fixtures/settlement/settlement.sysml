package Settlement {
    private import SI::*;
    private import ScalarValues::*;
    private import Time::TimeInstantValue;

    calc def mark { in label : String; return : Integer; }
    calc def ok { in label : String; return : Boolean; }
    calc def seen { in label : String; return : Boolean; }
    calc def note { in label : String; in value : Integer; return : Integer; }

    item def Marked { attribute n : Integer; }
    item def Stop { attribute id : Integer; attribute need : String; }
    item def Go;

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
}
