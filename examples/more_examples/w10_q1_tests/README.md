## Second example: a real week's official tests

`TestPointsLinkedList.java` and `TestSepChainingPoints.java` are the actual official
JUnit tests used for a "Week 10 Q1" (linked list / separate chaining) assignment,
included here as a second, more realistic example of what a week's `tests/` folder
looks like beyond the toy `Calculator` example.

These aren't wired into the one-command `examples/` demo (`examples/tests/Week3Test.java`)
because they test different classes (`PointsLinkedList2`, `SepChainingPoints`, `Point`,
etc.) that aren't part of the `Calculator` example submissions — mixing them in would
just produce compile errors.

To try these for real, point `--tests` at this folder and `--submissions` at a folder
containing a matching student submission (a zip, jar, or folder with `Point.java`,
`PointListNode.java`, `PointsLinkedList2.java`, `SepChainingPoints.java`, etc.):

```
python grade.py --tests examples/more_examples/w10_q1_tests --submissions <your submissions folder>
```
