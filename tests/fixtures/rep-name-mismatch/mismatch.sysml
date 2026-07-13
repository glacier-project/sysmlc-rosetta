package NameMismatch {
    private import ScalarValues::Real;

    // The body defines camelCase `restrictAngle`: the def name must
    // match the calc def name for the generated import to resolve.
    calc def restrict_angle {
        in theta : Real;
        return : Real;

        rep impl language "Python"
        /*
         * def restrictAngle(theta):
         *     return theta
         */
    }
}
