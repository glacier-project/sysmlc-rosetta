package DupA {
    private import ScalarValues::Real;

    // Identical duplicate helpers are harmless and stay allowed;
    // the collision check fires only when implementations differ.
    rep scaffolding language "Python"
    /*
     * def _sign(x):
     *     return float((x > 0.0) - (x < 0.0))
     */

    calc def use_sign {
        in x : Real;
        return : Real;

        rep impl language "Python"
        /*
         * def use_sign(x):
         *     return _sign(x)
         */
    }
}

package DupB {
    private import ScalarValues::Real;

    rep scaffolding language "Python"
    /*
     * def _sign(x):
     *     return float((x > 0.0) - (x < 0.0))
     */
}
