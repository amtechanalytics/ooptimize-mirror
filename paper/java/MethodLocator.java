import com.sun.source.tree.ClassTree;
import com.sun.source.tree.CompilationUnitTree;
import com.sun.source.tree.MethodTree;
import javax.lang.model.element.Modifier;
import com.sun.source.tree.NewClassTree;
import com.sun.source.tree.Tree;
import com.sun.source.tree.VariableTree;
import com.sun.source.util.JavacTask;
import com.sun.source.util.SourcePositions;
import com.sun.source.util.TreePathScanner;
import com.sun.source.util.Trees;

import javax.tools.DiagnosticCollector;
import javax.tools.JavaCompiler;
import javax.tools.JavaFileObject;
import javax.tools.SimpleJavaFileObject;
import javax.tools.StandardJavaFileManager;
import javax.tools.ToolProvider;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;
import java.util.Locale;

/**
 * Parses Java sources with the JDK's own parser (no attribution, no classpath) and prints one JSON line
 * per method declared in a named (non-anonymous) class:
 *   {"class":"pkg.Outer$Inner","simple":"Inner","name":"m","params":"int,java.util.List<String>",
 *    "start":123,"end":456,"line_start":10,"line_end":20,"body_start":200,"abstract":false,
 *    "static":false,"synchronized":true,"sig":"public synchronized List<String> m(int a)"}
 * Offsets are char offsets into the file decoded as UTF-8. Constructors are omitted.
 *
 *   java -cp helpers MethodLocator file1.java [file2.java ...]      one JSON line per method, per file
 *   java -cp helpers MethodLocator --snippet file.java              the file holds ONE method (no class);
 *                                                                   prints its line, or {"error": ...}
 * Exit 0 always for parse output; syntax errors are reported as {"file":..,"error":..} lines.
 */
public class MethodLocator {

    static String esc(String s) {
        StringBuilder b = new StringBuilder();
        for (char c : s.toCharArray()) {
            switch (c) {
                case '"': b.append("\\\""); break;
                case '\\': b.append("\\\\"); break;
                case '\n': b.append("\\n"); break;
                case '\r': b.append("\\r"); break;
                case '\t': b.append("\\t"); break;
                default:
                    if (c < 0x20) b.append(String.format("\\u%04x", (int) c)); else b.append(c);
            }
        }
        return b.toString();
    }

    static class Src extends SimpleJavaFileObject {
        final String text;
        Src(String name, String text) {
            super(URI.create("string:///" + name.replace('\\', '/')), JavaFileObject.Kind.SOURCE);
            this.text = text;
        }
        @Override public CharSequence getCharContent(boolean ignoreEncodingErrors) { return text; }
    }

    static List<String> scan(String fileLabel, String text, boolean snippet) throws Exception {
        JavaCompiler jc = ToolProvider.getSystemJavaCompiler();
        DiagnosticCollector<JavaFileObject> diags = new DiagnosticCollector<>();
        StandardJavaFileManager fm = jc.getStandardFileManager(diags, Locale.ROOT, StandardCharsets.UTF_8);
        String body = snippet ? "class __W {\n" + text + "\n}\n" : text;
        int shift = snippet ? "class __W {\n".length() : 0;
        Src src = new Src(snippet ? "__W.java" : "F.java", body);
        JavacTask task = (JavacTask) jc.getTask(null, fm, diags, List.of("-proc:none"), null, List.of(src));
        Iterable<? extends CompilationUnitTree> units = task.parse();
        List<String> out = new ArrayList<>();
        boolean syntaxError = diags.getDiagnostics().stream().anyMatch(d -> d.getKind() == javax.tools.Diagnostic.Kind.ERROR);
        if (syntaxError) {
            String msg = diags.getDiagnostics().get(0).getMessage(Locale.ROOT);
            out.add("{\"file\":\"" + esc(fileLabel) + "\",\"error\":\"" + esc(msg.length() > 300 ? msg.substring(0, 300) : msg) + "\"}");
            return out;
        }
        SourcePositions sp = Trees.instance(task).getSourcePositions();
        for (CompilationUnitTree cu : units) {
            String pkg = cu.getPackageName() == null ? "" : cu.getPackageName().toString();
            new TreePathScanner<Void, Void>() {
                final Deque<String> names = new ArrayDeque<>();
                int anon = 0;

                @Override public Void visitNewClass(NewClassTree nct, Void v) {
                    anon++;
                    try { return super.visitNewClass(nct, v); } finally { anon--; }
                }

                @Override public Void visitClass(ClassTree ct, Void v) {
                    String n = ct.getSimpleName().toString();
                    if (n.isEmpty() || anon > 0) {           // anonymous or nested inside an anonymous class
                        anon++;
                        try { return super.visitClass(ct, v); } finally { anon--; }
                    }
                    names.addLast(n);
                    try { return super.visitClass(ct, v); } finally { names.removeLast(); }
                }

                @Override public Void visitMethod(MethodTree mt, Void v) {
                    String name = mt.getName().toString();
                    if (anon == 0 && !name.equals("<init>") && !names.isEmpty()) {
                        long s = sp.getStartPosition(cu, mt), e = sp.getEndPosition(cu, mt);
                        long bs = mt.getBody() == null ? -1 : sp.getStartPosition(cu, mt.getBody());
                        StringBuilder params = new StringBuilder();
                        for (VariableTree p : mt.getParameters()) {
                            if (params.length() > 0) params.append(',');
                            params.append(p.getType().toString().replaceAll("\\s+", ""));
                        }
                        boolean isAbstract = mt.getBody() == null || mt.getModifiers().getFlags().contains(Modifier.ABSTRACT);
                        boolean isStatic = mt.getModifiers().getFlags().contains(Modifier.STATIC);
                        boolean isSync = mt.getModifiers().getFlags().contains(Modifier.SYNCHRONIZED);
                        long sigEnd = bs > 0 ? bs : e;
                        String sig = body.substring((int) s, (int) sigEnd).replaceAll("@\\w+(\\([^)]*\\))?", "").replaceAll("\\s+", " ").trim();
                        String cls = (pkg.isEmpty() ? "" : pkg + ".") + String.join("$", names);
                        long lineS = cu.getLineMap().getLineNumber(s), lineE = cu.getLineMap().getLineNumber(e);
                        out.add("{\"file\":\"" + esc(fileLabel) + "\",\"class\":\"" + esc(cls) + "\",\"simple\":\"" + esc(names.peekLast())
                                + "\",\"name\":\"" + esc(name) + "\",\"params\":\"" + esc(params.toString())
                                + "\",\"start\":" + (s - shift) + ",\"end\":" + (e - shift) + ",\"body_start\":" + (bs < 0 ? -1 : bs - shift)
                                + ",\"line_start\":" + (snippet ? lineS - 1 : lineS) + ",\"line_end\":" + (snippet ? lineE - 1 : lineE)
                                + ",\"abstract\":" + isAbstract + ",\"static\":" + isStatic + ",\"synchronized\":" + isSync
                                + ",\"returns\":\"" + esc(mt.getReturnType() == null ? "" : mt.getReturnType().toString()) + "\""
                                + ",\"sig\":\"" + esc(sig) + "\"}");
                    }
                    return super.visitMethod(mt, v);
                }
            }.scan(cu, null);
        }
        return out;
    }

    public static void main(String[] args) throws Exception {
        boolean snippet = args.length > 0 && args[0].equals("--snippet");
        int i = snippet ? 1 : 0;
        StringBuilder sb = new StringBuilder();
        for (; i < args.length; i++) {
            Path p = Paths.get(args[i]);
            String text;
            try {
                text = new String(Files.readAllBytes(p), StandardCharsets.UTF_8);
            } catch (Exception ex) {
                sb.append("{\"file\":\"").append(esc(args[i])).append("\",\"error\":\"unreadable\"}\n");
                continue;
            }
            for (String line : scan(args[i], text, snippet)) sb.append(line).append('\n');
        }
        System.out.print(sb);
        System.out.flush();
    }
}
