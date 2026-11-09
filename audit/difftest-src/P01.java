public class P01 {
protected static byte[] sortSmallByteArray(
      byte[] key, Object[] children, int left, int right) { // x
    for (int i = left; i < right; i++) {
      byte ai = key[i + 1];
      int u = ai & 0xFF;
      if (u >= (key[i] & 0xFF)) {
        // already in place, no shifting or rewrite needed
        continue;
      }
      Object child = children[i + 1];
      int j = i;
      do {
        key[j + 1] = key[j];
        children[j + 1] = children[j];
        j--;
      } while (j >= left && u < (key[j] & 0xFF));
      key[j + 1] = ai;
      children[j + 1] = child;
    }
    return key;
  }
}
