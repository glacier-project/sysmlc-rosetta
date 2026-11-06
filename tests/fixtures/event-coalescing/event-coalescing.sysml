package EventCoalescing {
    private import ScalarValues::*;
    private import SI::*;
    item def Pulse { attribute value : Integer; }

    state def DoubleSend {
        port tx;
        entry; then idle;
        state idle;
        state sent;
        transition first idle accept after 0.1 [s]
            do action {
                send new Pulse(value = 1) via tx;
                then send new Pulse(value = 2) via tx;
            } then sent;
    }

    state def SeparatedSends {
        port tx;
        entry; then idle;
        state idle { entry send new Pulse(value = 1) via tx; }
        state sent { entry send new Pulse(value = 2) via tx; }
        transition first idle accept after 0 [s] then sent;
    }

    state def SiblingSends {
        port tx;
        entry; then working;
        state working parallel {
            state left {
                entry; then idle;
                state idle;
                state sent;
                transition first idle accept after 0.1 [s]
                    do send new Pulse(value = 1) via tx then sent;
            }
            state right {
                entry; then idle;
                state idle;
                state sent;
                transition first idle accept after 0.1 [s]
                    do send new Pulse(value = 2) via tx then sent;
            }
        }
    }

    state def Receiver {
        port rx;
        entry; then waitingFirst;
        state waitingFirst;
        state second;
        state verified;
        transition first waitingFirst accept p : Pulse via rx
            if p.value == 1 then second;
        transition first second accept p : Pulse via rx
            if p.value == 2 then verified;
    }

    state def SelfSend {
        port selfPort;
        entry; then idle;
        state idle;
        state waitingFirst;
        state second;
        state verified;
        transition first idle accept after 0.1 [s]
            do action {
                send new Pulse(value = 1) to selfPort;
                then send new Pulse(value = 2) to selfPort;
            } then waitingFirst;
        transition first waitingFirst accept p : Pulse
            if p.value == 1 then second;
        transition first second accept p : Pulse
            if p.value == 2 then verified;
    }

    part def DoubleSender { port tx; exhibit state : DoubleSend; }
    part def SiblingSender { port tx; exhibit state : SiblingSends; }
    part def SeparatedSender { port tx; exhibit state : SeparatedSends; }
    part def Sink { port rx; exhibit state : Receiver; }

    part direct {
        part sender : DoubleSender;
        part sink : Sink;
        connect sender.tx to sink.rx;
    }
    part siblings {
        part sender : SiblingSender;
        part sink : Sink;
        connect sender.tx to sink.rx;
    }
    part separated {
        part sender : SeparatedSender;
        part sink : Sink;
        connect sender.tx to sink.rx;
    }
}
