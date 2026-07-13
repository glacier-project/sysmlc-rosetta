package StarProof {
    private import ScalarValues::Real;

    // The starred-unpacking line legitimately starts with `*`: it must
    // survive extraction verbatim (star and relative indent intact).
    calc def unpack_last {
        return : Real;

        rep impl language "Python"
        /*
         * def unpack_last():
         *     values = [1.0, 2.0, 3.0]
         *     *head, tail = values
         *     return tail
         */
    }
}
