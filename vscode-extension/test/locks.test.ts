import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { unwritable } from "../src/locks";

function tmp(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "dsa-grading-locks-"));
}

test("a writable file and a file that doesn't exist yet are both fine", () => {
  const dir = tmp();
  const there = path.join(dir, "grades.csv");
  fs.writeFileSync(there, "a,b\n");
  assert.deepEqual(unwritable([there, path.join(dir, "mcvScore.csv.bak")]), []);
});

test("checking a file never changes it", () => {
  const dir = tmp();
  const file = path.join(dir, "grades.csv");
  fs.writeFileSync(file, "a,b\n");
  fs.utimesSync(file, 1000, 1000);
  const before = fs.statSync(file);
  unwritable([file]);
  const after = fs.statSync(file);
  assert.equal(after.mtimeMs, before.mtimeMs);
  assert.equal(after.size, before.size);
  assert.equal(fs.readFileSync(file, "utf8"), "a,b\n");
});

// The Excel case: an open handle that refuses writers. A read-only file refuses
// the same open, with the same consequence for the script (it can't write), and
// can be made without a second program. Root ignores file modes, so skip there.
test("a file that can't be opened for writing is reported, the others are not", { skip: process.getuid?.() === 0 }, () => {
  const dir = tmp();
  const locked = path.join(dir, "grades.csv");
  const fine = path.join(dir, "mcvScore.csv");
  fs.writeFileSync(locked, "a,b\n");
  fs.writeFileSync(fine, "1,2\n");
  fs.chmodSync(locked, 0o444);
  try {
    assert.deepEqual(unwritable([locked, fine]), [locked]);
  } finally {
    fs.chmodSync(locked, 0o644);
  }
});
