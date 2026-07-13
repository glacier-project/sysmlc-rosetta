package Scaffold {
    private import ScalarValues::Real;

    // Module scaffolding: shared import, constant, and private helper
    // live once on the package; each calc def carries only its own def.
    rep scaffolding language "Python"
    /*
     * import math
     *
     * FACTOR = 2.0
     *
     * def _twice(x):
     *     return FACTOR * x
     */

    calc def quadruple {
        in x : Real;
        return : Real;

        rep impl language "Python"
        /*
         * def quadruple(x):
         *     return _twice(_twice(x))
         */
    }
}
