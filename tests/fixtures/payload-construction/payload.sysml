package PayloadConstruction {
    private import ScalarValues::*;

    // Evaluation-control instrumentation backed by payload_evaluations.py.
    // KerML functions may have side effects; `sample` counts each runtime
    // evaluation of a send argument and folds the count into its result,
    // and `evaluations` reports the count. Neither is modeled behavior.
    calc def sample { in value : Integer; return : Integer; }
    calc def evaluations { return : Integer; }

    item def Data {
        attribute x : Integer default 7;
        attribute y : Integer default 9;
        attribute z : Integer default 5;
    }
    attribute def Sample {
        attribute level : Integer default 3;
        attribute spare : Integer;
    }
    item def Base {
        attribute s : Sample;
    }
    item def Envelope :> Base;
    item def Derived {
        attribute a : Integer default 1;
        attribute b : Integer default a + 1;
    }

    // Named arguments in reverse field order, both reading the sender's
    // state, with z omitted. Each argument is evaluated once, at the send,
    // in the written order: y = 40 * 10 + 1, x = 40 * 10 + 2, two calls.
    state def EvaluationOnce {
        attribute current : Integer := 1;
        attribute capturedX : Integer := -1;
        attribute capturedY : Integer := -1;
        attribute capturedZ : Integer := -1;
        attribute calls : Integer := -1;
        port commPort;
        entry;
            then idle;
        state idle {
            entry assign current := 40;
        }
        state armed {
            entry assign current := 100;
        }
        state received {
            entry assign calls := evaluations();
        }
        state verified;
        transition first idle
            do send new Data(y = sample(current), x = sample(current))
                to commPort
            then armed;
        transition first armed
            accept r : Data via commPort
            do action {
                assign capturedX := r.x;
                then assign capturedY := r.y;
                then assign capturedZ := r.z;
            }
            then received;
        transition first received
            if capturedX == 402 and capturedY == 401 and capturedZ == 5
                and calls == 2
            then verified;
    }

    // Every field is inherited; the structured field takes the values of
    // its definition's fields, and its unvalued spare stays unspecified.
    state def InheritedStructured {
        attribute level : Integer := -1;
        port commPort;
        entry;
            then idle;
        state idle;
        state armed;
        state received;
        state verified;
        transition first idle
            do send new Envelope() to commPort
            then armed;
        transition first armed
            accept r : Envelope via commPort
            do assign level := r.s.level
            then received;
        transition first received if level == 3 then verified;
    }

    // An omitted field whose type value refers to another field cannot be
    // evaluated with the send's arguments.
    state def NonConstantDefault {
        port commPort;
        entry;
            then idle;
        state idle;
        state armed;
        transition first idle
            do send new Derived() to commPort
            then armed;
    }

    // A connected peer receives the constructed value, sampled when the
    // sender performs the send: y = 40 + 2, x and z from their defaults.
    state def PeerSenderBehavior {
        attribute current : Integer := 1;
        port outPort;
        entry;
            then idle;
        state idle {
            entry assign current := 40;
        }
        state sent {
            entry assign current := 100;
        }
        transition first idle
            do send new Data(y = current + 2) via outPort
            then sent;
    }
    state def PeerReceiverBehavior {
        attribute capturedX : Integer := -1;
        attribute capturedY : Integer := -1;
        attribute capturedZ : Integer := -1;
        port rxPort;
        entry;
            then waiting;
        state waiting;
        state received;
        state verified;
        transition first waiting
            accept r : Data via rxPort
            do action {
                assign capturedX := r.x;
                then assign capturedY := r.y;
                then assign capturedZ := r.z;
            }
            then received;
        transition first received
            if capturedX == 7 and capturedY == 42 and capturedZ == 5
            then verified;
    }

    // Without arguments the connected peer receives every default.
    state def DefaultSenderBehavior {
        port outPort;
        entry;
            then idle;
        state idle;
        state sent;
        transition first idle
            do send new Data() via outPort
            then sent;
    }
    state def DefaultReceiverBehavior {
        attribute capturedX : Integer := -1;
        attribute capturedY : Integer := -1;
        attribute capturedZ : Integer := -1;
        port rxPort;
        entry;
            then waiting;
        state waiting;
        state received;
        state verified;
        transition first waiting
            accept r : Data via rxPort
            do action {
                assign capturedX := r.x;
                then assign capturedY := r.y;
                then assign capturedZ := r.z;
            }
            then received;
        transition first received
            if capturedX == 7 and capturedY == 9 and capturedZ == 5
            then verified;
    }

    part def PeerSender {
        port outPort;
        exhibit state : PeerSenderBehavior;
    }
    part def PeerReceiver {
        port rxPort;
        exhibit state : PeerReceiverBehavior;
    }

    part def DefaultSender {
        port outPort;
        exhibit state : DefaultSenderBehavior;
    }
    part def DefaultReceiver {
        port rxPort;
        exhibit state : DefaultReceiverBehavior;
    }

    part peerSystem {
        part tx : PeerSender;
        part rx : PeerReceiver;
        part defaultTx : DefaultSender;
        part defaultRx : DefaultReceiver;
        connect tx.outPort to rx.rxPort;
        connect defaultTx.outPort to defaultRx.rxPort;
    }
}
