package RepOnAction {
    // A Python rep on an action def must be rejected: its statements
    // would land at module top level and execute at import time.
    action def logIt {
        language "Python"
        /*
         * print("hi")
         */
    }
}
