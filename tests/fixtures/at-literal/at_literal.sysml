// An `accept at` argument that isn't a bare attribute reference (here, an
// attribute plus a literal offset) is folded to a constant at compile time
// and exercises the literal-float branch of the `at` renderer, as opposed to
// `accept at deadlineTime` (a bare `FeatureReferenceExpression`), which goes
// through the attribute-reference expression branch instead. A raw literal
// like `accept at 6 [s]` does not type-check on its own: `accept at` requires
// a `TimeInstantValue`, and a bare `<n> [s]` literal only infers as a
// duration quantity, so the literal instant must be produced by folding an
// arithmetic expression rooted at a `TimeInstantValue`-typed attribute.
package AtLiteral {
    private import SI::*;
    private import ScalarValues::*;
    private import ISQ::DurationValue;
    private import Time::TimeInstantValue;

    state def Machine {
        attribute deadlineTime : TimeInstantValue default 4 [s];
        entry;
            then idle;
        state idle;
        state running;
        transition first idle accept at deadlineTime + 2 [s] then running;
        transition first running then done;
    }
}
