package StateNames {
    private import SI::*;
    private import ScalarValues::*;

    part def Device { exhibit state : Flat; }
    part def device { exhibit state : Collision; }
    part caseSystem {
        part upper : Device;
        part lower : device;
    }

    part def header_TYPES { exhibit state : Flat; }
    part header { part child : header_TYPES; }

    part def multi_TYPES {
        exhibit state one : Flat;
        exhibit state two : Collision;
    }
    part multi { part child : multi_TYPES; }

    state def Flat {
        entry; then 'S1.1';
        state 'S1.1';
        state 'S1.2';
        transition first 'S1.1' accept after 0.001 [s] then 'S1.2';
    }

    state def Nested {
        entry; then 'Outer.1';
        state 'Outer.1' {
            entry; then 'Inner.1';
            state 'Inner.1';
            state 'Inner.2';
            transition first 'Inner.1' accept after 0.001 [s] then 'Inner.2';
            transition first 'Inner.2' accept after 0.001 [s] then done;
        }
        state 'Finished.1';
        transition first 'Outer.1' then 'Finished.1';
        transition first 'Outer.1' accept after 0.005 [s] then 'Finished.1';
    }

    state def Collision {
        entry; then 'S1.1';
        state 'S1.1';
        state S1_1;
    }

    state def TimerCollision {
        entry; then S1;
        state S1;
        state 't.S1';
        transition first S1 accept after 0.001 [s] then 't.S1';
    }

    state def InstanceCollision {
        entry; then 'Outer.1';
        state 'Outer.1' {
            entry; then idle;
            state idle;
        }
        state 'c.Outer.1';
    }

    state def AttributeCollision {
        attribute S1_1 : Integer := 0;
        entry; then 'S1.1';
        state 'S1.1';
    }

    state def ReactorCollision {
        entry; then a_b;
        state a_b {
            entry; then c;
            state c {
                entry; then idle;
                state idle;
            }
        }
        state a {
            entry; then b_c;
            state b_c {
                entry; then idle;
                state idle;
            }
        }
    }

    state def GeneratedTimerCollision {
        entry; then 'S1.1';
        state 'S1.1';
        state S1;
        state finished;
        transition first 'S1.1' accept after 0.001 [s] then S1;
        transition first S1 accept after 0.001 [s] then finished;
        transition first S1 accept after 0.002 [s] then finished;
    }

    state def Repeated {
        entry; then 'Outer.1';
        state 'Outer.1' {
            entry; then 'Inner.1';
            state 'Inner.1';
        }
        state 'Outer.2' {
            entry; then 'Inner.1';
            state 'Inner.1';
        }
    }

    state def ReactorCaseCollision {
        entry; then 'Outer.1';
        state 'Outer.1' {
            entry; then idle;
            state idle;
        }
        state outer_1 {
            entry; then idle;
            state idle;
        }
    }

    state def PriorityCollision {
        entry; then 'S1.1';
        state 'S1.1';
        state S1_1;
        state S1_1_01;
        transition first 'S1.1' accept after 0.001 [s] then S1_1;
        transition first S1_1 accept after 0.001 [s] then S1_1_01;
    }

    state def InternalCollision {
        attribute _Outer_busy : Integer := 11;
        attribute _Outer_c_Outer_busy : Integer := 13;
        attribute _Outer_pending : Integer := 17;
        attribute _Outer_sel : Integer := 19;
        entry; then Outer;
        state Outer {
            entry; then Inner;
            state Inner;
            transition first Inner accept after 0.001 [s] then done;
        }
        state Finished;
        state Corrupted;
        transition first Outer
            if _Outer_busy == 11 and _Outer_c_Outer_busy == 13
                and _Outer_pending == 17 and _Outer_sel == 19
            then Finished;
        transition first Outer then Corrupted;
        transition first Outer accept after 0.005 [s] then Finished;
    }

    state def ExitCollision {
        entry; then Outer;
        state Outer {
            entry; then exit_0;
            state exit_0;
            state exit_0_done;
            transition first exit_0 accept after 0.001 [s] then Finished;
        }
        state Finished;
        transition first Outer accept after 0.005 [s] then Finished;
    }

    item def exit_0_01 { attribute value : Integer; }

    state def ExitRoleCollision {
        entry; then Outer;
        state Outer {
            entry; then 'exit.0';
            state 'exit.0';
            transition first 'exit.0' accept after 0.001 [s] then Finished;
        }
        state Waiting;
        state Finished;
        transition first Waiting accept exit_0_01 then Finished;
    }

    item def Tick;

    state def LocalCollision {
        entry; then Outer;
        state Outer {
            entry; then Inner;
            state Inner;
        }
        state _value;
        transition first Outer accept reading : Tick then _value;
    }

    item def Tick_origin;

    state def OriginPortCollision {
        entry; then working;
        state working parallel {
            state r1 {
                entry; then a;
                state a;
                transition first a accept Tick then a;
            }
            state r2 {
                entry; then b;
                state b;
                transition first b accept Tick then b;
                transition first b accept Tick_origin then b;
            }
        }
    }

    state def EscapedLabels {
        entry; then 'Out"er';
        state 'Out"er' {
            entry; then 'a\\b';
            state 'a\\b';
            state 'it\'s';
            transition first 'a\\b' accept after 0.001 [s] then 'it\'s';
            transition first 'it\'s' accept after 0.001 [s] then done;
        }
        state finished;
        transition first 'Out"er' then finished;
    }

    state def UcFields {
        attribute ready : Boolean := false;
        entry; then t;
        state t;
        state fired;
        state finished;
        transition first t accept after 0.001 [s] then fired;
        transition first t accept when ready then finished;
        transition first fired accept after 0.001 [s] then finished;
    }

    state def UcAttributeTimer {
        attribute t_idle : Integer := 7;
        entry; then idle;
        state idle;
        state finished;
        transition first idle accept after 0.001 [s]
            if t_idle == 7 then finished;
    }

    state def TypesHeader {
        entry; then types;
        state types {
            entry; then idle;
            state idle;
            transition first idle accept after 0.001 [s] then done;
        }
        state finished;
        transition first types then finished;
    }

    state def ParallelInstance {
        entry; then P;
        state P parallel {
            state P {
                entry; then a;
                state a;
            }
            state Q {
                entry; then b;
                state b;
            }
        }
    }
}
