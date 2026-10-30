package TypedDispatch {
    private import ScalarValues::*;
    private import SI::*;
    item def Base { attribute value : Integer default 41; }
    item def Child :> Base;
    item def Other { attribute value : Integer default 99; }
    package A { item def Alarm { attribute value : Integer default 17; } }
    package B { item def Alarm { attribute value : Integer default 29; } }
    state def External {
        attribute count : Integer := 0;
        attribute captured : Integer := -1;
        entry; then waiting;
        state waiting;
        state received;
        state verified;
        transition first waiting accept packet : Base
            do action { assign count := count + 1;
                then assign captured := packet.value; }
            then received;
        transition first received if count == 1 and captured == 41
            then verified;
    }
    state def Collisions {
        attribute a : Integer := -1;
        attribute b : Integer := -1;
        entry; then waiting;
        state waiting;
        state firstReceived;
        state secondReceived;
        state verified;
        transition first waiting accept packet : A::Alarm
            do assign a := packet.value then firstReceived;
        transition first firstReceived accept packet : B::Alarm
            do assign b := packet.value then secondReceived;
        transition first secondReceived if a == 17 and b == 29 then verified;
    }
    state def Nested {
        entry; then outer;
        state outer {
            entry; then inner;
            state inner;
            state selected;
            transition first inner accept Child then selected;
        }
        state aborted;
        transition first outer accept Base then aborted;
    }
    state def Sender {
        port p;
        entry; then idle;
        state idle;
        state sent;
        transition first idle accept after 0.1 [s]
            do send new Child() via p then sent;
    }
    state def Receiver {
        attribute count : Integer := 0;
        attribute captured : Integer := -1;
        port p;
        entry; then waiting;
        state waiting;
        state received;
        state verified;
        state duplicate;
        transition first waiting accept packet : Base via p
            do action { assign count := count + 1;
                then assign captured := packet.value; } then received;
        transition first waiting accept packet : Child via p then duplicate;
        transition first received if count == 1 and captured == 41 then verified;
        transition first verified accept Base via p then duplicate;
    }
    state def UnrelatedReceiver {
        port p;
        entry; then waiting;
        state waiting;
        state wrong;
        transition first waiting accept Other via p then wrong;
    }
    part def SenderPart { port p; exhibit state behavior : Sender; }
    part def ReceiverPart { port p; exhibit state behavior : Receiver; }
    part def UnrelatedPart { port p; exhibit state behavior : UnrelatedReceiver; }
    part system {
        part sender : SenderPart;
        part receiver : ReceiverPart;
        part unrelated : UnrelatedPart;
        connect sender.p to receiver.p;
        connect sender.p to unrelated.p;
    }
    item boundValue : Base;
    state def UnsupportedBound {
        entry; then idle;
        state idle;
        transition first idle accept packet : Base = boundValue then idle;
    }
    state def ConstructCollisions {
        port p;
        attribute a : Integer := -1;
        attribute b : Integer := -1;
        entry; then idle;
        state idle;
        state waitingA;
        state waitingB;
        state received;
        state verified;
        transition first idle do send new A::Alarm() to p then waitingA;
        transition first waitingA accept packet : A::Alarm
            do action { assign a := packet.value;
                then send new B::Alarm() to p; } then waitingB;
        transition first waitingB accept packet : B::Alarm
            do assign b := packet.value then received;
        transition first received if a == 17 and b == 29 then verified;
    }

    state def NestedDeclaredFirst {
        entry; then outer;
        state outer {
            entry; then inner;
            state inner;
            state localSelected;
            state parentSelected;
            transition first inner accept Base then localSelected;
        }
        transition first outer::inner accept Child then outer::parentSelected;
    }
    state def ParentDeclaredFirst {
        entry; then outer;
        transition first outer::inner accept Child then outer::parentSelected;
        state outer {
            entry; then inner;
            state inner;
            state localSelected;
            state parentSelected;
            transition first inner accept Base then localSelected;
        }
    }

}
