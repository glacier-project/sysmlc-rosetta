package Collision {
    private import ScalarValues::Real;

    // Two calc defs share the simple name `step` with different
    // bodies: the flat module namespace cannot host both.
    package Simulation {
        calc def step {
            in x : Real;
            return : Real;

            rep impl language "Python"
            /*
             * def step(x):
             *     return x + 1.0
             */
        }
    }

    package Controller {
        calc def step {
            in u : Real;
            return : Real;

            rep impl language "Python"
            /*
             * def step(u):
             *     return u * 0.5
             */
        }
    }
}
