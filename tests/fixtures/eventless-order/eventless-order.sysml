package EventlessOrder {
    // Same-source eventless alternatives declared in different lexical
    // scopes. The first eligible declaration wins; false guards are skipped.

    // Inner-scope transition declared first: expect outer.b.
    state def NestedFirst {
        entry; then outer;
        state outer {
            entry; then a;
            state a;
            state b;
            state c;
            transition first a then b;
        }
        transition first outer.a then outer.c;
    }

    // Root-scope transition declared first: expect outer.c.
    state def ParentFirst {
        entry; then outer;
        transition first outer.a then outer.c;
        state outer {
            entry; then a;
            state a;
            state b;
            state c;
            transition first a then b;
        }
    }

    // Inner false guard declared first is skipped; the next declared,
    // true-guarded inner transition wins over the later root one: expect
    // outer.b.
    state def NestedFalseFirst {
        entry; then outer;
        state outer {
            entry; then a;
            state a;
            state b;
            state c;
            transition first a if false then c;
            transition first a if true then b;
        }
        transition first outer.a then outer.c;
    }
}
