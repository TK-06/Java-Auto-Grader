// Which output files a script could not write to right now. No vscode import
// (test/locks.test.ts runs it on temp files).
//
// grade.py and check_lateness.py stop at once when an output CSV can't be
// opened for writing, and on Windows that is nearly always Excel holding the
// file open. Asking first turns a failure in the terminal into one sentence.
//
// The probe opens the file for read+write and closes it again: it never writes,
// so the content and the modified time are untouched. A file that doesn't exist
// yet is fine - the script creates it.

import * as fs from "fs";

export function unwritable(files: string[]): string[] {
  return files.filter((file) => {
    try {
      fs.closeSync(fs.openSync(file, "r+"));
      return false;
    } catch (err) {
      return (err as NodeJS.ErrnoException).code !== "ENOENT";
    }
  });
}
