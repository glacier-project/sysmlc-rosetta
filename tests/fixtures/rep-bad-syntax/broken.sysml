package BadSyntax {
    private import ScalarValues::Real;

    // The def line is missing its colon: collection must fail naming
    // this calc def, not degrade into a missing-import error later.
    calc def broken {
        in x : Real;
        return : Real;

        rep impl language "Python"
        /*
         * def broken(x)
         *     return x + 1
         */
    }
}
