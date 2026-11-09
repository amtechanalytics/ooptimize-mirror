public class P07 {
protected static byte[] sortSmallByteArray(
      byte[] key, Object[] children, int left, int right) { // x
    for (int i = left; i < right; i++) {
      final byte ai = key[i + 1];
      final int unsignedAi = ai & 0xFF;
      int j = i;
      byte kj = key[j];
      if (unsignedAi >= (kj & 0xFF)) {
        continue;
      }
      final Object child = children[i + 1];
      while (true) {
        key[j + 1] = kj;
        children[j + 1] = children[j];
        j--;
        if (j < left) {
          break;
        }
        kj = key[j];
        if (unsignedAi >= (kj & 0xFF)) {
          break;
        }
      }
      key[j + 1] = ai;
      children[j + 1] = child;
    }
    return key;
  }
}
