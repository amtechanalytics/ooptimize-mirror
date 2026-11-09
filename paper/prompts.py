"""The generation prompt: one stable prefix (byte-identical for every call, so vendor prompt caching applies)
and an item per (method, rung). The target method is always last in the item. The reply schema is strict on
the four keys and lenient on wrapping (code fences or prose around the JSON object are tolerated)."""
from __future__ import annotations

import json
import re

CHANGES = ["ALGORITHMIC", "ALLOCATION", "CACHING", "SYNCHRONIZATION", "COPY_ELISION", "LOOP", "DATA_STRUCTURE", "NONE"]
CONFIDENCE = ["LOW", "MEDIUM", "HIGH"]
MAX_METHOD_CHARS = 60000

PREFIX = """You are a senior Java performance engineer working on a widely used open-source library. You will be shown one
method from the library's main source code, sometimes with surrounding code for context. The method appears on a
hot path in the library's own JMH benchmarks. Your task is to rewrite that one method so that it runs faster or
allocates less, while keeping it a drop-in replacement for the original.

Rules for the rewrite:
1. Return the complete rewritten method: its annotations, modifiers, signature and body, exactly as it should
   appear in the source file in place of the original method. Do not return the enclosing class.
2. Keep the method's name, parameter types, return type, declared exceptions, static-ness and visibility unchanged.
   Callers and overriding classes elsewhere in the library must still compile without any change.
3. You may only change the body of this one method. You may not add fields, imports, other methods or new
   classes. Use fully qualified names for any type that the file may not already import.
4. Java language level: write code that compiles at the language level the library already uses in the shown
   code; do not introduce newer syntax or newer library APIs than the surrounding code uses.
5. Prefer changes with a real, measurable benefit on a hot path: fewer allocations, fewer copies, fewer
   redundant checks, better loops, cheaper data-structure operations, avoiding boxing, avoiding repeated work.
6. If you believe the method is already as fast as it can reasonably be, return it unchanged and set
   "claimed_change" to "NONE".
7. Keep the method's Javadoc out of the answer (the existing comment above the method stays in the file);
   ordinary comments inside the body are allowed but not required.
8. Do not add logging, printing, assertions that were not there, timing code, or benchmark annotations.

How your answer is used: the text of "patched_method" replaces the original method in the source file,
character for character, between the first character of its modifiers or annotations and its closing brace.
The rest of the file is not changed. The file is then compiled against the library exactly as it was built
before, so the rewrite must compile in that file, with that file's imports and that file's fields and helper
methods, without anything else being edited. Indentation does not matter; it is not reformatted.

About the context you may be given: the input names the library, the source file and the class, and then
says which context is included. "The method alone" means only the method's own text is shown. "The enclosing
source file" means the whole file is shown (long files are shortened around the method, with /* ... */ marking
the cuts). "Related types and call sites" adds, for supertypes and subtypes of the class found in the library,
their declarations without bodies, and short excerpts of other files that call a method with this name. The
context is there for you to read; you still return only the one target method.

Answer with exactly one JSON object and nothing else. No markdown, no code fences, no commentary before or after.
The object has exactly these four keys:
{
  "patched_method": "<the complete rewritten method as Java source text, with \\n for line breaks>",
  "rationale": "<at most three sentences: what you changed and why it is faster>",
  "claimed_change": "<one of ALGORITHMIC | ALLOCATION | CACHING | SYNCHRONIZATION | COPY_ELISION | LOOP | DATA_STRUCTURE | NONE>",
  "confidence": "<one of LOW | MEDIUM | HIGH: how sure you are that the rewrite is faster and still correct>"
}
Meaning of the claimed_change values:
- ALGORITHMIC: a different algorithm or a lower complexity for the same result.
- ALLOCATION: fewer or smaller objects or arrays allocated per call.
- CACHING: reusing a previously computed value instead of recomputing it.
- SYNCHRONIZATION: cheaper or narrower locking, volatile access or atomic operations.
- COPY_ELISION: avoiding a copy of an array, collection or buffer.
- LOOP: loop restructuring such as hoisting, fusion, unrolling or early exit.
- DATA_STRUCTURE: a cheaper operation on, or a cheaper choice of, a collection or array.
- NONE: no change; the method is returned exactly as given.
Use the single value that best describes the main change.

Example 1 (a rewrite).
Input method:
    public int countPositive(java.util.List<Integer> values) {
        int n = 0;
        for (Integer v : new java.util.ArrayList<>(values)) {
            if (v.intValue() > 0) {
                n = n + 1;
            }
        }
        return n;
    }
Answer:
{"patched_method": "public int countPositive(java.util.List<Integer> values) {\\n    int n = 0;\\n    for (int i = 0, size = values.size(); i < size; i++) {\\n        if (values.get(i) > 0) {\\n            n++;\\n        }\\n    }\\n    return n;\\n}", "rationale": "Removes the per-call copy of the list and the iterator allocation, indexing directly instead.", "claimed_change": "COPY_ELISION", "confidence": "MEDIUM"}

Example 2 (no change).
Input method:
    public static int mix(int h) {
        return h ^ (h >>> 16);
    }
Answer:
{"patched_method": "public static int mix(int h) {\\n    return h ^ (h >>> 16);\\n}", "rationale": "A single shift and xor; there is nothing cheaper that keeps the same result.", "claimed_change": "NONE", "confidence": "HIGH"}

The examples only show the answer format; they say nothing about which kind of change suits the method below.
Everything after this line is the task: the library, the file, the class, optional context, and last the target
method to rewrite. Answer only with the JSON object described above.
"""

RUNG_TITLE = {"R1": "no additional context (the method alone)",
              "R2": "the enclosing source file (shortened around the method if long)",
              "R3": "the enclosing source file, the class's supertypes and subtypes in the library (signatures only), "
                    "and call sites of the method elsewhere in the library"}


def render_item(m: dict, rung: str) -> str:
    parts = [f"Library: {m['lib']}", f"File: {m['file']}", f"Class: {m['class'].replace('$', '.')}",
             f"Context given: {RUNG_TITLE[rung]}", ""]
    if rung in ("R2", "R3"):
        parts += ["### Enclosing source file", "```java", m.get("ctx_r2", ""), "```", ""]
    if rung == "R3":
        parts += ["### Related types and call sites", "```java", m.get("ctx_r3", ""), "```", ""]
    parts += ["### Target method (rewrite this one method)", "```java", m["src"], "```"]
    return "\n".join(parts)


def _extract_object(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```\s*$", "", t)
    try:
        d = json.loads(t)
        if isinstance(d, dict):
            return d
    except ValueError:
        pass
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("no JSON object in the reply")
    d = json.loads(t[a:b + 1])
    if not isinstance(d, dict):
        raise ValueError("reply JSON is not an object")
    return d


def parse_generate(text: str) -> dict:
    d = _extract_object(text)
    pm = d.get("patched_method")
    if not isinstance(pm, str) or not pm.strip():
        raise ValueError("patched_method missing or empty")
    if len(pm) > MAX_METHOD_CHARS:
        raise ValueError(f"patched_method longer than {MAX_METHOD_CHARS} chars")
    if "{" not in pm or "}" not in pm:
        raise ValueError("patched_method has no method body")
    cc = str(d.get("claimed_change", "")).strip().upper()
    if cc not in CHANGES:
        raise ValueError(f"claimed_change {d.get('claimed_change')!r} not in {CHANGES}")
    cf = str(d.get("confidence", "")).strip().upper()
    if cf not in CONFIDENCE:
        raise ValueError(f"confidence {d.get('confidence')!r} not in {CONFIDENCE}")
    pm = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", pm.strip())
    return {"patched_method": pm, "rationale": str(d.get("rationale", ""))[:2000], "claimed_change": cc,
            "confidence": cf}
