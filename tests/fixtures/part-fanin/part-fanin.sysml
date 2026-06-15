// Two sources send Sig into one sink's single input -> single-channel
// fan-in, which LF forbids (nondeterministic). The assembler must reject it
// and point at multiplicity (a bank into a multiport).
package PartFanin {
    item def Sig;

    state def SourceBehavior {
        port tx;
        entry; then run;
        state run;
        state sent;
        transition first run do send new Sig() via tx then sent;
    }
    state def SinkBehavior {
        port rx;
        entry; then waiting;
        state waiting;
        state got;
        transition first waiting accept Sig via rx then got;
    }

    part def Source1 { port tx; exhibit state : SourceBehavior; }
    part def Source2 { port tx; exhibit state : SourceBehavior; }
    part def Sink    { port rx; exhibit state : SinkBehavior; }

    part sys {
        part s1 : Source1;
        part s2 : Source2;
        part sink : Sink;
        connect s1.tx to sink.rx;
        connect s2.tx to sink.rx;
    }
}
