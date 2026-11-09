public class Sc01 {
protected static byte[] sortSmallByteArray(
      byte[] key, Object[] children, int left, int right) { // x
    for (int i = left; i < right; i++) {
      byte ai = key[i + 1];
      int unsignedAi = ai & 0xFF;
      int j = i;
      if (unsignedAi >= (key[j] & 0xFF)) {
        continue;
      }
      Object child = children[i + 1];
      while (j >= left && unsignedAi < (key[j] & 0xFF)) {
        key[j + 1] = key[j];
        children[j + 1] = children[j];
        j--;
      }
      key[j + 1] = ai;
      children[j + 1] = child;
    }
    return key;
  }
}
