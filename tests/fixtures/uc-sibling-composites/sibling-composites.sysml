package SiblingComposites {
    item def Go;
    item def Swap;
    item def Stop;

    // `busy` exits on Stop from either composite substate, so both of
    // them drain their own children inside the same scope.
    state def Machine {
        entry;
            then idle;

        state idle;

        state busy {
            entry;
                then one;

            state one {
                entry;
                    then a;
                state a;
                state b;
                transition first a accept Go then b;
            }

            state two {
                entry;
                    then c;
                state c;
            }

            transition first one accept Swap then two;
        }

        transition first idle accept Go then busy;
        transition first busy accept Stop then idle;
    }
}
