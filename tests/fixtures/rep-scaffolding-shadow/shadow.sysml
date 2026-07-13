package Shadow {
    private import ScalarValues::Real;

    // The scaffolding helper shares the calc def's name with a
    // different body: one of the two would silently win.
    rep scaffolding language "Python"
    /*
     * def gain(x):
     *     return 2.0 * x
     */

    calc def gain {
        in x : Real;
        return : Real;

        rep impl language "Python"
        /*
         * def gain(x):
         *     return 3.0 * x
         */
    }
}
