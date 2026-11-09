public class Sc09 {
protected static byte[] sortSmallByteArray(
      byte[] key, Object[] children, int left, int right) { // x
    for (int i = left; i < right; i++) {
      final byte ai = key[i + 1];
      final int unsignedByteAi = ai & 0xFF;
      int j = i;
      if (unsignedByteAi >= (key[j] & 0xFF)) {
        // already in place, no moves or writes needed
        continue;
      }
      final Object child = children[i + 1];
      while (true) {
        key[j + 1] = key[j];
        children[j + 1] = children[j];
        if (j == left) {
          j--;
          break;
        }
        j--;
        if (unsignedByteAi >= (key[j] & 0xFF)) {
          break;
        }
      }
      key[j + 1] = ai;
      children[j + 1] = child;
    }
    return key;
  }
}
