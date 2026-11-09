import java.io.PrintStream;
import java.lang.reflect.Array;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/**
 * Runs test classes and writes one JSON file with a per-test outcome, using reflection only so this
 * helper compiles against the JDK alone and works with whatever test framework is on the classpath:
 *   - JUnit Platform (Jupiter, Vintage, TestNG engine ...) when org.junit.platform.launcher.core.LauncherFactory
 *     is present (engine = "platform");
 *   - otherwise JUnit 4 JUnitCore (engine = "junit4").
 *   java -cp <test cp>:<helpers> OoptTestRunner [--engine auto|platform|junit4] --out result.json Cls1 Cls2 ...
 * Output: {"engine":"...","run":N,"failed":F,"skipped":S,"tests":[{"id":"Cls#m","status":"PASS|FAIL|SKIP","message":"..."}],
 *          "error":null|"..."}
 * Exit code 0 whatever the test outcome; non-zero only if the runner itself could not run.
 */
public class OoptTestRunner {

    static String esc(String s) {
        if (s == null) return "";
        StringBuilder b = new StringBuilder();
        for (char c : s.toCharArray()) {
            if (c == '"') b.append("\\\"");
            else if (c == '\\') b.append("\\\\");
            else if (c < 0x20) b.append(' ');
            else b.append(c);
        }
        String r = b.toString();
        return r.length() > 600 ? r.substring(0, 600) : r;
    }

    static final Map<String, String[]> results = new LinkedHashMap<>();   // id -> {status, message}

    public static void main(String[] args) throws Exception {
        String engine = "auto", out = null;
        List<String> classes = new ArrayList<>();
        for (int i = 0; i < args.length; i++) {
            if (args[i].equals("--engine")) engine = args[++i];
            else if (args[i].equals("--out")) out = args[++i];
            else classes.add(args[i]);
        }
        if (out == null) { System.err.println("--out required"); System.exit(2); }
        // keep the tests' own output away from stdout parsing
        PrintStream realOut = System.out;
        String error = null;
        String used = engine;
        try {
            if (engine.equals("auto")) {
                used = present("org.junit.platform.launcher.core.LauncherFactory") ? "platform" : "junit4";
            }
            if (used.equals("platform")) runPlatform(classes); else runJUnit4(classes);
        } catch (Throwable t) {
            error = t.getClass().getName() + ": " + t.getMessage();
        } finally {
            System.setOut(realOut);
        }
        int run = 0, failed = 0, skipped = 0;
        StringBuilder tests = new StringBuilder();
        for (Map.Entry<String, String[]> e : results.entrySet()) {
            String st = e.getValue()[0];
            if (st.equals("SKIP")) skipped++; else run++;
            if (st.equals("FAIL")) failed++;
            if (tests.length() > 0) tests.append(',');
            tests.append("{\"id\":\"").append(esc(e.getKey())).append("\",\"status\":\"").append(st)
                 .append("\",\"message\":\"").append(esc(e.getValue()[1])).append("\"}");
        }
        String json = "{\"engine\":\"" + used + "\",\"run\":" + run + ",\"failed\":" + failed + ",\"skipped\":" + skipped
                + ",\"tests\":[" + tests + "],\"error\":" + (error == null ? "null" : "\"" + esc(error) + "\"") + "}\n";
        Files.write(Paths.get(out), json.getBytes(StandardCharsets.UTF_8));
        System.exit(error == null ? 0 : 3);
    }

    static boolean present(String cls) {
        try { Class.forName(cls, false, OoptTestRunner.class.getClassLoader()); return true; }
        catch (Throwable t) { return false; }
    }

    // ------------------------------------------------------------------ JUnit Platform via reflection
    static void runPlatform(List<String> classes) throws Exception {
        ClassLoader cl = OoptTestRunner.class.getClassLoader();
        Class<?> sels = Class.forName("org.junit.platform.engine.discovery.DiscoverySelectors", true, cl);
        Class<?> selIface = Class.forName("org.junit.platform.engine.DiscoverySelector", true, cl);
        Method selectClass = sels.getMethod("selectClass", String.class);
        List<Object> selectors = new ArrayList<>();
        for (String c : classes) selectors.add(selectClass.invoke(null, c));
        Class<?> rb = Class.forName("org.junit.platform.launcher.core.LauncherDiscoveryRequestBuilder", true, cl);
        Object builder = rb.getMethod("request").invoke(null);
        builder = rb.getMethod("selectors", List.class).invoke(builder, selectors);
        Object request = rb.getMethod("build").invoke(builder);
        Class<?> lf = Class.forName("org.junit.platform.launcher.core.LauncherFactory", true, cl);
        Object launcher = lf.getMethod("create").invoke(null);
        Class<?> listenerIface = Class.forName("org.junit.platform.launcher.TestExecutionListener", true, cl);
        InvocationHandler h = (proxy, m, a) -> {
            String n = m.getName();
            if (n.equals("executionFinished") && a != null && a.length == 2) {
                Object id = a[0], res = a[1];
                if ((Boolean) id.getClass().getMethod("isTest").invoke(id)) {
                    String key = testKey(id);
                    String status = res.getClass().getMethod("getStatus").invoke(res).toString();
                    String msg = "";
                    Object opt = res.getClass().getMethod("getThrowable").invoke(res);
                    if (opt instanceof Optional && ((Optional<?>) opt).isPresent()) {
                        Throwable t = (Throwable) ((Optional<?>) opt).get();
                        msg = t.getClass().getName() + ": " + t.getMessage();
                    }
                    results.put(key, new String[]{status.equals("SUCCESSFUL") ? "PASS" : (status.equals("ABORTED") ? "SKIP" : "FAIL"), msg});
                }
            } else if (n.equals("executionSkipped") && a != null && a.length == 2) {
                Object id = a[0];
                if ((Boolean) id.getClass().getMethod("isTest").invoke(id)) results.put(testKey(id), new String[]{"SKIP", String.valueOf(a[1])});
            } else if (n.equals("equals")) {
                return proxy == a[0];
            } else if (n.equals("hashCode")) {
                return System.identityHashCode(proxy);
            } else if (n.equals("toString")) {
                return "OoptListener";
            }
            return null;
        };
        Object listener = Proxy.newProxyInstance(cl, new Class<?>[]{listenerIface}, h);
        Object arr = Array.newInstance(listenerIface, 1);
        Array.set(arr, 0, listener);
        Class<?> reqIface = Class.forName("org.junit.platform.launcher.LauncherDiscoveryRequest", true, cl);
        // LauncherFactory returns a package-private class (SessionPerRequestLauncher): the method must be looked up on
        // the public Launcher interface, or reflection fails with IllegalAccessException (7 Nov canary).
        Class<?> launcherIface = Class.forName("org.junit.platform.launcher.Launcher", true, cl);
        launcherIface.getMethod("execute", reqIface, arr.getClass()).invoke(launcher, request, arr);
        if (selectors.isEmpty() && selIface == null) throw new IllegalStateException("no selectors");
    }

    static String testKey(Object id) throws Exception {
        String uid = String.valueOf(id.getClass().getMethod("getUniqueId").invoke(id));
        String legacy = String.valueOf(id.getClass().getMethod("getLegacyReportingName").invoke(id));
        // the class segment of the unique id: [class:pkg.Cls] / [testclass:...]; the method segment: [method:m()] / [test:...]
        String cls = seg(uid, "class:");
        if (cls == null) cls = seg(uid, "testclass:");
        if (cls == null) cls = seg(uid, "runner:");
        String rest = uid.substring(uid.lastIndexOf('/') + 1);
        return (cls == null ? "?" : cls) + "#" + (legacy.isEmpty() ? rest : legacy) + "|" + Integer.toHexString(uid.hashCode());
    }

    static String seg(String uid, String tag) {
        int i = uid.indexOf("[" + tag);
        if (i < 0) return null;
        int j = uid.indexOf(']', i);
        return uid.substring(i + tag.length() + 1, j);
    }

    // ------------------------------------------------------------------ JUnit 4 via reflection
    static void runJUnit4(List<String> classes) throws Exception {
        ClassLoader cl = OoptTestRunner.class.getClassLoader();
        Class<?> core = Class.forName("org.junit.runner.JUnitCore", true, cl);
        Class<?> testAnn = Class.forName("org.junit.Test", true, cl);
        @SuppressWarnings("unchecked") Class<? extends java.lang.annotation.Annotation> ignoreAnn =
                (Class<? extends java.lang.annotation.Annotation>) Class.forName("org.junit.Ignore", true, cl);
        List<Class<?>> loaded = new ArrayList<>();
        for (String c : classes) {
            Class<?> k = Class.forName(c, true, cl);
            loaded.add(k);
            for (Method m : k.getMethods()) {
                @SuppressWarnings("unchecked") Class<? extends java.lang.annotation.Annotation> ta =
                        (Class<? extends java.lang.annotation.Annotation>) testAnn;
                if (m.isAnnotationPresent(ta)) {
                    boolean ignored = m.isAnnotationPresent(ignoreAnn) || k.isAnnotationPresent(ignoreAnn);
                    results.put(k.getName() + "#" + m.getName(), new String[]{ignored ? "SKIP" : "PASS", ""});
                }
            }
        }
        Object arr = Array.newInstance(Class.class, loaded.size());
        for (int i = 0; i < loaded.size(); i++) Array.set(arr, i, loaded.get(i));
        PrintStream devnull = new PrintStream(new java.io.OutputStream() { public void write(int b) {} });
        System.setOut(devnull);
        Object result = core.getMethod("runClasses", Class[].class).invoke(null, arr);
        Collection<?> failures = (Collection<?>) result.getClass().getMethod("getFailures").invoke(result);
        for (Object f : failures) {
            Object d = f.getClass().getMethod("getDescription").invoke(f);
            String cls = String.valueOf(d.getClass().getMethod("getClassName").invoke(d));
            Object mn = d.getClass().getMethod("getMethodName").invoke(d);
            String msg;
            Throwable t = (Throwable) f.getClass().getMethod("getException").invoke(f);
            msg = t == null ? String.valueOf(f.getClass().getMethod("getMessage").invoke(f)) : t.getClass().getName() + ": " + t.getMessage();
            String key = cls + "#" + (mn == null ? "<class>" : mn.toString());
            results.put(key, new String[]{"FAIL", msg});
        }
    }
}
