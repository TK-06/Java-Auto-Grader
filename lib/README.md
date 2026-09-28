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

## License

This jar is not covered by the repository's MIT License. It is the JUnit Platform
Console Launcher, distributed under the Eclipse Public License 2.0, and it bundles
components under their own licenses: JUnit 4 (Eclipse Public License 1.0), Hamcrest
(BSD), and picocli and univocity-parsers (Apache License 2.0). The full text of each
license is included inside the jar. Keep this section accurate when bumping the jar.
