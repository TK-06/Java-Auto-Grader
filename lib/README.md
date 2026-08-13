## JUnit Console Launcher jar

`grade.py` needs the JUnit Platform Console Launcher standalone jar in this folder.
`junit-platform-console-standalone-1.14.0.jar` is already committed here, so a fresh
clone works with no setup step. The instructions below are only for bumping to a newer
version later.

**Option A — Maven Central (recommended):**
Download a `junit-platform-console-standalone-*.jar` from
https://search.maven.org/artifact/org.junit.platform/junit-platform-console-standalone
and drop it here.

**Option B — apt (Ubuntu/Debian):**
```
sudo apt-get install junit5
cp /usr/share/java/junit-platform-console-standalone-*.jar lib/
```

Only keep **one** jar in this folder — `grade.py` picks the jar whose name
contains `console-standalone`, and errors out if it finds more than one
match or none at all.
