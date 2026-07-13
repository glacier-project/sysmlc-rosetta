package TwoBodies {
    private import ScalarValues::Real;

    // Two Python reps on one calc def: ambiguous, keep exactly one.
    calc def double {
        in x : Real;
        return : Real;

        rep one language "Python"
        /*
         * def double(x):
         *     return x * 2
         */

        rep other language "Python"
        /*
         * def double(x):
         *     return x + x
         */
    }
}
