public class P17 {
public static int advanceUntil(char[] array, int pos, int length, char min) {
    int start = pos + 1;
    if (start >= length) {
      return length;
    }
    int idx = java.util.Arrays.binarySearch(array, start, length, min);
    if (idx >= 0) {
      return idx;
    }
    return -idx - 1;
  }
}
