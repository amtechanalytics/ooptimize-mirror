"""The five subject libraries: build commands, module layout and test framework hints. Kept in code (not
config/) so the repair step may fix a build command; the pinned commits themselves live in
config/data_pins.json and are verified by the data check before any build."""
from __future__ import annotations

MVN_COMMON = ("-B -q -DskipTests -Dcheckstyle.skip -Denforcer.skip -Dmaven.javadoc.skip=true -Dmaven.source.skip=true "
              "-Djacoco.skip=true -Drevapi.skip=true -Djapicmp.skip=true -Danimal.sniffer.skip=true -Dspotbugs.skip=true "
              "-Dpmd.skip=true -Dforbiddenapis.skip=true -Dgpg.skip=true -Dmaven.test.skip.exec=true")
GRADLE_COMMON = ("--no-daemon --no-configuration-cache --no-build-cache -q "
                 "-Porg.gradle.java.installations.auto-download=false -Porg.gradle.java.installations.auto-detect=false "
                 "-Porg.gradle.java.installations.paths={java_home}")
GRADLE_CAFFEINE = ("--no-daemon --no-configuration-cache --no-build-cache -q "
                   "-Porg.gradle.java.installations.auto-download=true -Porg.gradle.java.installations.auto-detect=false "
                   "-Porg.gradle.java.installations.paths={java_home}")

LIBS = {
    "eclipse-collections": {
        "pin": "lib_eclipse_collections",
        "tool": "maven",
        "release": 17,
        "build": [f"{{mvn}} {MVN_COMMON} -T 1C install -pl eclipse-collections-code-generator,"
                  "eclipse-collections-code-generator-maven-plugin,eclipse-collections-api,eclipse-collections,"
                  "eclipse-collections-testutils,unit-tests -am",
                  f"{{mvn}} {MVN_COMMON} -Pall package -pl jmh-tests -am"],
        "jmh_jar": "jmh-tests/target/microbenchmarks.jar",
        # source root -> (module that compiles it, module whose tests exercise it)
        "roots": {"eclipse-collections/src/main/java": ("eclipse-collections", "unit-tests"),
                  "eclipse-collections-api/src/main/java": ("eclipse-collections-api", "unit-tests")},
        "packages": ["org.eclipse.collections"],
    },
    "RoaringBitmap": {
        "pin": "lib_roaringbitmap",
        "tool": "gradle",
        "release": 8,
        "build": [f"./gradlew {GRADLE_COMMON} :roaringbitmap:testClasses :bsi:testClasses :jmh:shadowJar"],
        "jmh_jar": "jmh/build/libs/benchmarks.jar",
        "roots": {"roaringbitmap/src/main/java": ("roaringbitmap", "roaringbitmap"),
                  "bsi/src/main/java": ("bsi", "bsi")},
        "packages": ["org.roaringbitmap"],
    },
    "netty": {
        "pin": "lib_netty",
        "tool": "maven",
        "release": 8,
        # '!linux' switches off the profiles that compile the JNI transports (no C toolchain on the instance);
        # the microbench jar then carries the Java-only epoll/kqueue jars and native-transport benchmarks fail
        # individually, which the selector tolerates.
        "build": [f"{{mvn}} {MVN_COMMON} -T 1C install '-P!linux' -pl common,buffer,codec-base,codec,codec-http,"
                  "handler,transport -am",
                  f"{{mvn}} {MVN_COMMON} -T 1C package '-Pbenchmark-jar,!linux' -pl microbench -am"],
        "jmh_jar": "microbench/target/microbenchmarks.jar",
        "roots": {f"{m}/src/main/java": (m, m) for m in ("common", "buffer", "codec-base", "codec", "codec-http",
                                                          "handler", "transport")},
        "packages": ["io.netty"],
    },
    "JCTools": {
        "pin": "lib_jctools",
        "tool": "maven",
        "release": 8,
        "build": [f"{{mvn}} {MVN_COMMON} install -pl jctools-core,jctools-experimental -am",
                  f"{{mvn}} {MVN_COMMON} package -pl jctools-benchmarks -am"],
        "jmh_jar": "jctools-benchmarks/target/microbenchmarks.jar",
        "roots": {"jctools-core/src/main/java": ("jctools-core", "jctools-core"),
                  "jctools-experimental/src/main/java": ("jctools-experimental", "jctools-experimental")},
        "packages": ["org.jctools"],
    },
    "caffeine": {
        "pin": "lib_caffeine",
        "tool": "gradle",
        "release": 21,           # built with -PjavaVersion=21 (the build's default toolchain is 11, absent here)
        # its build logic runs on JDK 25 and compiles with max(javaVersion, 26) through Gradle toolchains (foojay resolver
        # in its settings); auto-provisioning is allowed for caffeine only. The study's own compiles and runs use the
        # pinned JDK 21, with -source/-target taken from the built class files (7 Nov canary: "no matching toolchain").
        "build": [f"./gradlew {GRADLE_CAFFEINE} -PjavaVersion=21 -Plint=false :caffeine:testClasses :caffeine:jmhJar"],
        "jmh_jar_glob": "caffeine/build/libs/*-jmh.jar",
        "gradle_flags": GRADLE_CAFFEINE,
        "roots": {"caffeine/src/main/java": ("caffeine", "caffeine")},
        "packages": ["com.github.benmanes.caffeine"],
    },
}

ORDER = ["eclipse-collections", "RoaringBitmap", "netty", "JCTools", "caffeine"]
PILOT_LIBRARY_DEFAULT = "JCTools"           # smallest build; the pilot exercises every oracle on it


def module_for(lib: str, rel_path: str) -> tuple[str, str, str] | None:
    """(source root, compile module, test module) for a source file path relative to the library root."""
    for root, (mod, tmod) in LIBS[lib]["roots"].items():
        if rel_path.startswith(root + "/"):
            return root, mod, tmod
    return None
