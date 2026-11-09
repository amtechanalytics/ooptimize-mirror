public class P15 {
protected static byte[] sortSmallByteArray(
      byte[] key, Object[] children, int left, int right) { // x
    for (int i = left; i < right; i++) {
      final byte ai = key[i + 1];
      final int unsignedAi = ai & 0xFF;
      int j = i;
      if (unsignedAi >= (key[j] & 0xFF)) {
        // already in place, no writes needed
        continue;
      }
      final Object child = children[i + 1];
      do {
        key[j + 1] = key[j];
        children[j + 1] = children[j];
        j--;
      } while (j >= left && unsignedAi < (key[j] & 0xFF));
      key[j + 1] = ai;
      children[j + 1] = child;
    }
    return key;
  }
}
