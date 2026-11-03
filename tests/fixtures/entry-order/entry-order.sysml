package EntryOrder {
    private import SI::*;
    private import ScalarValues::*;

    // Test-only observer in entry_marks.py; logs send argument evaluation.
    calc def mark { in label : String; return : Integer; }

    item def Marked { attribute n : Integer; }
    item def Go;

    state def Nested {
        port selfPort;
        entry send new Marked(n = mark("root")) to selfPort;
            then outer;
        state outer {
            entry action {
                send new Marked(n = mark("outer.entry.first")) to selfPort;
                send new Marked(n = mark("outer.entry.last")) to selfPort;
            }
                then mid;
            do send new Marked(n = mark("outer.do")) to selfPort;
            state mid {
                entry send new Marked(n = mark("mid")) to selfPort;
                    then leaf;
                state leaf {
                    entry send new Marked(n = mark("leaf")) to selfPort;
                }
            }
        }
        state away {
            entry send new Marked(n = mark("away")) to selfPort;
        }
        transition first outer accept after 0.1 [s] then away;
        transition first away accept after 0.1 [s] then outer;
    }

    state def ZeroTimer {
        port selfPort;
        entry;
            then outer;
        state outer {
            entry send new Marked(n = mark("outer")) to selfPort;
                then waiting;
            state waiting {
                entry send new Marked(n = mark("waiting")) to selfPort;
            }
            state timed {
                entry send new Marked(n = mark("timed")) to selfPort;
            }
            transition first waiting accept after 0 [s] then timed;
        }
        state away {
            entry send new Marked(n = mark("away")) to selfPort;
        }
        transition first outer accept after 0.1 [s] then away;
        transition first away accept after 0.1 [s] then outer;
    }

    state def SameTagInput {
        port selfPort;
        entry;
            then outer;
        state outer {
            entry send new Marked(n = mark("outer")) to selfPort;
                then waiting;
            state waiting {
                entry send new Marked(n = mark("waiting")) to selfPort;
            }
            state got {
                entry send new Marked(n = mark("got")) to selfPort;
            }
            transition first waiting accept Go then got;
        }
    }

    state def ParallelNested {
        port selfPort;
        entry;
            then both;
        state both parallel {
            entry send new Marked(n = mark("both")) to selfPort;
            do send new Marked(n = mark("both.do")) to selfPort;
            state r1 {
                entry send new Marked(n = mark("r1")) to selfPort;
                    then a;
                state a {
                    entry send new Marked(n = mark("r1.a")) to selfPort;
                }
            }
            state r2 {
                entry send new Marked(n = mark("r2")) to selfPort;
                    then b;
                state b {
                    entry send new Marked(n = mark("r2.b")) to selfPort;
                }
            }
        }
        state away {
            entry send new Marked(n = mark("away")) to selfPort;
        }
        transition first both accept after 0.1 [s] then away;
        transition first away accept after 0.1 [s] then both;
    }

    state def ParallelRoot parallel {
        port selfPort;
        entry send new Marked(n = mark("root")) to selfPort;
        do send new Marked(n = mark("root.do")) to selfPort;
        state r1 {
            entry send new Marked(n = mark("r1")) to selfPort;
                then a;
            do send new Marked(n = mark("r1.do")) to selfPort;
            state a {
                entry send new Marked(n = mark("r1.a")) to selfPort;
            }
        }
        state r2 {
            entry send new Marked(n = mark("r2")) to selfPort;
                then b;
            state b {
                entry send new Marked(n = mark("r2.b")) to selfPort;
            }
        }
    }

    state def ParallelRootEmpty parallel {
        port selfPort;
        state r1 {
            entry;
                then a;
            state a {
                entry send new Marked(n = mark("r1.a")) to selfPort;
            }
        }
        state r2 {
            entry;
                then b;
            state b {
                entry send new Marked(n = mark("r2.b")) to selfPort;
            }
        }
    }

    state def EmptyParent {
        port selfPort;
        entry;
            then outer;
        state outer {
            entry;
                then leaf;
            state leaf {
                entry send new Marked(n = mark("leaf")) to selfPort;
            }
        }
    }

    state def ReservedState {
        entry;
            then outer;
        state outer {
            entry;
                then _entry_order;
            state _entry_order;
        }
    }
}
