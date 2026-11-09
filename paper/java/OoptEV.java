import java.lang.reflect.Array;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.List;

/**
 * EqualsVerifier under a fixed configuration, via reflection (compiles against the JDK only).
 *   java -cp <cp>:<equalsverifier-nodep.jar>:<helpers> OoptEV <class fqn> [lenient|getclass]
 * Prints exactly one line:
 *   EV <class> PASS <config>
 *   EV <class> FAIL <config> <first 600 chars of the message>
 *   EV <class> NA <reason>      (no own equals/hashCode, class not loadable, EqualsVerifier absent, other error)
 * The same configuration is used on the original and on the patched classes; the oracle fires only when the
 * original PASSes and the patched build FAILs under the identical configuration.
 */
public class OoptEV {
    static final String[] LENIENT = {"STRICT_INHERITANCE", "NONFINAL_FIELDS", "ALL_FIELDS_SHOULD_BE_USED",
            "STRICT_HASHCODE", "NULL_FIELDS", "REFERENCE_EQUALITY", "TRANSIENT_FIELDS",
            "INHERITED_DIRECTLY_FROM_OBJECT", "SURROGATE_OR_BUSINESS_KEY", "BIGDECIMAL_EQUALITY"};
    // SURROGATE_KEY is omitted: EqualsVerifier rejects it together with SURROGATE_OR_BUSINESS_KEY, which covers it.

    public static void main(String[] args) {
        String cls = args[0];
        String cfg = args.length > 1 ? args[1] : "lenient";
        Class<?> target;
        try { target = Class.forName(cls, false, OoptEV.class.getClassLoader()); }
        catch (Throwable t) { System.out.println("EV " + cls + " NA cannot_load " + t.getClass().getSimpleName()); return; }
        try {
            boolean own;
            try {
                own = target.getMethod("equals", Object.class).getDeclaringClass() == target
                        || target.getMethod("hashCode").getDeclaringClass() == target;
            } catch (NoSuchMethodException e) { own = false; }
            if (!own) { System.out.println("EV " + cls + " NA no_own_equals"); return; }
            Class<?> ev;
            try { ev = Class.forName("nl.jqno.equalsverifier.EqualsVerifier"); }
            catch (ClassNotFoundException e) { System.out.println("EV " + cls + " NA ev_missing"); return; }
            Object api = ev.getMethod("forClass", Class.class).invoke(null, target);
            if (cfg.equals("lenient") || cfg.equals("getclass")) {
                Class<?> warning = Class.forName("nl.jqno.equalsverifier.Warning");
                List<Object> ws = new ArrayList<>();
                for (Object c : warning.getEnumConstants()) {
                    for (String n : LENIENT) if (((Enum<?>) c).name().equals(n)) ws.add(c);
                }
                Object arr = Array.newInstance(warning, ws.size());
                for (int i = 0; i < ws.size(); i++) Array.set(arr, i, ws.get(i));
                Method suppress = find(api.getClass(), "suppress", arr.getClass());
                api = suppress.invoke(api, arr);
            }
            if (cfg.equals("getclass")) api = find(api.getClass(), "usingGetClass").invoke(api);
            find(api.getClass(), "verify").invoke(api);
            System.out.println("EV " + cls + " PASS " + cfg);
        } catch (InvocationTargetException ite) {
            Throwable t = ite.getCause();
            if (t instanceof AssertionError) {
                String m = t.getMessage() == null ? "" : t.getMessage().replace('\n', ' ');
                if (m.length() > 600) m = m.substring(0, 600);
                System.out.println("EV " + cls + " FAIL " + cfg + " " + m);
            } else {
                String m = t == null || t.getMessage() == null ? "" : " " + t.getMessage().replace('\n', ' ');
                if (m.length() > 300) m = m.substring(0, 300);
                System.out.println("EV " + cls + " NA error " + (t == null ? "?" : t.getClass().getName()) + m);
            }
        } catch (Throwable t) {
            System.out.println("EV " + cls + " NA error " + t.getClass().getName());
        }
    }

    static Method find(Class<?> k, String name, Class<?>... types) throws NoSuchMethodException {
        for (Class<?> c = k; c != null; c = c.getSuperclass()) {
            for (Method m : c.getMethods()) {
                if (m.getName().equals(name) && m.getParameterCount() == types.length) {
                    boolean ok = true;
                    for (int i = 0; i < types.length; i++) if (!m.getParameterTypes()[i].isAssignableFrom(types[i])) ok = false;
                    if (ok) { m.setAccessible(true); return m; }
                }
            }
        }
        throw new NoSuchMethodException(name);
    }
}
