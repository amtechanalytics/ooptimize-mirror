public class P09 {
protected static int hybridUnsignedBinarySearch(final char[] array, final int begin, final int end, final char k) {
    // fast path: value would be inserted at the end
    if (end > 0 && array[end - 1] < k) {
        return -end - 1;
    }
    int low = begin;
    int high = end - 1;
    // binary search while the range is larger than a cache line (32 entries)
    while (low + 32 <= high) {
        int middle = (low + high) >>> 1;
        char midVal = array[middle];
        if (midVal < k) {
            low = middle + 1;
        } else if (midVal > k) {
            high = middle - 1;
        } else {
            return middle;
        }
    }
    // finish with a sequential scan
    for (int i = low; i <= high; i++) {
        char v = array[i];
        if (v >= k) {
            return (v == k) ? i : -(i + 1);
        }
    }
    // insertion point is after high
    return -(high + 2);
}
}
