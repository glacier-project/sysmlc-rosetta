package ActionOrder {
    private import ScalarValues::*;
    state def Declaration {
        attribute c : Integer := 0;
        entry;
        then idle;
        state idle {
            entry action {
                action multiply assign c := c * 10;
                action initialize assign c := 1;
            }
        }
    }
    state def Reversed {
        attribute c : Integer := 0;
        entry;
        then idle;
        state idle {
            entry action {
                action multiply assign c := c * 10;
                action initialize assign c := 1;
                first initialize then multiply;
            }
        }
    }
    state def Boundaries {
        attribute c : Integer := 0;
        entry;
        then idle;
        state idle {
            entry action {
                action multiply assign c := c * 10;
                action initialize assign c := 1;
                first start then initialize;
                first initialize then multiply;
                first multiply then done;
            }
        }
    }
    state def Then {
        attribute c : Integer := 0;
        entry;
        then idle;
        state idle {
            entry action {
                first start;
                then assign c := 1;
                then assign c := c * 10;
                then done;
            }
        }
    }
    state def Partial {
        attribute c : Integer := 0;
        entry;
        then idle;
        state idle {
            entry action {
                action multiply assign c := c * 10;
                action initialize assign c := 1;
                action extra assign c := 3;
                first initialize then multiply;
            }
        }
    }
    state def Branch {
        attribute c : Integer := 0;
        entry;
        then idle;
        state idle {
            entry action {
                action multiply assign c := c * 10;
                action initialize assign c := 1;
                action extra assign c := 3;
                first initialize then multiply;
                first initialize then extra;
            }
        }
    }
    state def Merge {
        attribute c : Integer := 0;
        entry;
        then idle;
        state idle {
            entry action {
                action multiply assign c := c * 10;
                action initialize assign c := 1;
                action extra assign c := 3;
                first initialize then multiply;
                first extra then multiply;
            }
        }
    }
    state def Cycle {
        attribute c : Integer := 0;
        entry;
        then idle;
        state idle {
            entry action {
                action multiply assign c := c * 10;
                action initialize assign c := 1;
                first initialize then multiply;
                first multiply then initialize;
            }
        }
    }
    state def Disconnected {
        attribute c : Integer := 0;
        entry;
        then idle;
        state idle {
            entry action {
                action multiply assign c := c * 10;
                action initialize assign c := 1;
                action extra assign c := 3;
                action last assign c := 4;
                first initialize then multiply;
                first extra then last;
            }
        }
    }
    state def WrongStart {
        attribute c : Integer := 0;
        entry;
        then idle;
        state idle {
            entry action {
                action multiply assign c := c * 10;
                action initialize assign c := 1;
                first initialize then start;
                first start then multiply;
            }
        }
    }
    state def WrongDone {
        attribute c : Integer := 0;
        entry;
        then idle;
        state idle {
            entry action {
                action multiply assign c := c * 10;
                action initialize assign c := 1;
                first initialize then done;
                first done then multiply;
            }
        }
    }
    state def Guarded {
        attribute c : Integer := 0;
        entry;
        then idle;
        state idle {
            entry action {
                action multiply assign c := c * 10;
                action initialize assign c := 1;
                first initialize if true then multiply;
            }
        }
    }
    state def Chained {
        attribute c : Integer := 0;
        entry;
        then idle;
        state idle {
            entry action {
                action multiply assign c := c * 10;
                action initialize assign c := 1;
                first initialize.done then multiply.start;
            }
        }
    }
    state def Foreign {
        attribute c : Integer := 0;
        entry;
        then idle;
        state idle {
            entry action {
                action multiply assign c := c * 10;
                action initialize assign c := 1;
                ref occurrence other;
                first other then initialize;
                first initialize then multiply;
            }
        }
    }
    state def Multiplicity {
        attribute c : Integer := 0;
        entry;
        then idle;
        state idle {
            entry action {
                action multiply assign c := c * 10;
                action initialize assign c := 1;
                succession optional [0..1] first initialize then multiply;
            }
        }
    }
    state def EndMultiplicity {
        attribute c : Integer := 0;
        entry;
        then idle;
        state idle {
            entry action {
                action multiply assign c := c * 10;
                action initialize assign c := 1;
                first [0..1] initialize then multiply;
            }
        }
    }
    state def UnitMultiplicity {
        attribute c : Integer := 0;
        entry;
        then idle;
        state idle {
            entry action {
                action multiply assign c := c * 10;
                action initialize assign c := 1;
                succession single [1] first [1] initialize then [1] multiply;
            }
        }
    }
    state def ActionMultiplicity {
        attribute c : Integer := 0;
        entry;
        then idle;
        state idle {
            entry action {
                action multiply [2] assign c := c * 10;
                action initialize assign c := 1;
                first initialize then multiply;
            }
        }
    }
    state def SuccessionFlow {
        attribute c : Integer := 0;
        entry;
        then idle;
        state idle {
            entry action {
                action producer {
                    out item x;
                }
                action consumer {
                    in item x;
                }
                succession flow from producer.x to consumer.x;
            }
        }
    }
    state def Duplicate {
        attribute c : Integer := 0;
        entry;
        then idle;
        state idle {
            entry action {
                action multiply assign c := c * 10;
                action initialize assign c := 1;
                first initialize then multiply;
                first initialize then multiply;
            }
        }
    }
    state def Empty {
        attribute c : Integer := 0;
        entry;
        then idle;
        state idle {
            entry action {
                first start then done;
            }
        }
    }
    state def RuntimeEntry {
        attribute c : Integer := 0;
        entry;
        then idle;
        state idle {
            entry action {
                action multiply assign c := c * 10;
                action initialize assign c := 1;
                first initialize then multiply;
            }
        }
        transition first idle then checked;
        state checked;
        transition first checked if c == 10 then success;
        transition first checked if c != 10 then failure;
        state success;
        state failure;
    }
    state def RuntimeExit {
        attribute c : Integer := 0;
        entry;
        then idle;
        state idle {
            exit action {
                action multiply assign c := c * 10;
                action initialize assign c := 1;
                first initialize then multiply;
            }
        }
        transition first idle then checked;
        state checked;
        transition first checked if c == 10 then success;
        transition first checked if c != 10 then failure;
        state success;
        state failure;
    }
    state def RuntimeDo {
        attribute c : Integer := 0;
        entry;
        then idle;
        state idle {
            do action {
                action multiply assign c := c * 10;
                action initialize assign c := 1;
                first initialize then multiply;
            }
        }
        transition first idle then checked;
        state checked;
        transition first checked if c == 10 then success;
        transition first checked if c != 10 then failure;
        state success;
        state failure;
    }
    state def RuntimeEffect {
        attribute c : Integer := 0;
        entry;
        then idle;
        state idle {
        }
        transition first idle do action {
            action multiply assign c := c * 10;
            action initialize assign c := 1;
            first initialize then multiply;
        }
        then checked;
        state checked;
        transition first checked if c == 10 then success;
        transition first checked if c != 10 then failure;
        state success;
        state failure;
    }
    state def RuntimeDeclaration {
        attribute c : Integer := 0;
        entry;
        then idle;
        state idle {
            entry action {
                action multiply assign c := c * 10;
                action initialize assign c := 1;
            }
        }
        transition first idle then checked;
        state checked;
        transition first checked if c == 1 then success;
        transition first checked if c != 1 then failure;
        state success;
        state failure;
    }
    state def RuntimeThen {
        attribute c : Integer := 0;
        entry;
        then idle;
        state idle {
            entry action {
                first start;
                then assign c := 1;
                then assign c := c * 10;
                then done;
            }
        }
        transition first idle then checked;
        state checked;
        transition first checked if c == 10 then success;
        transition first checked if c != 10 then failure;
        state success;
        state failure;
    }
    state def RuntimeBoundaries {
        attribute c : Integer := 0;
        entry;
        then idle;
        state idle {
            entry action {
                action multiply assign c := c * 10;
                action initialize assign c := 1;
                first start then initialize;
                first initialize then multiply;
                first multiply then done;
            }
        }
        transition first idle then checked;
        state checked;
        transition first checked if c == 10 then success;
        transition first checked if c != 10 then failure;
        state success;
        state failure;
    }
    item def Reading {
        attribute value : Integer;
    }
    state def RuntimeSend {
        attribute c : Integer := 0;
        port p;
        entry;
        then idle;
        state idle;
        transition first idle do action {
            action dispatch send new Reading(c) to p;
            action initialize assign c := 10;
            first initialize then dispatch;
        }
        then checked;
        state checked;
        transition first checked accept reading : Reading via p if reading.value == 10 then success;
        transition first checked accept reading : Reading via p if reading.value != 10 then failure;
        state success;
        state failure;
    }
}
