public class C02 { static final int MAX_CAPACITY=65536; long[] bitmap; int cardinality; C02(long[] b,int c){bitmap=b;cardinality=c;}
int cardinalityInRange(int start, int end) {
    if ((cardinality == 0 || cardinality == MAX_CAPACITY)
        && start >= 0 && start <= end && end <= MAX_CAPACITY) {
      return cardinality == 0 ? 0 : end - start;
    }
    if (cardinality != -1 && end - start > MAX_CAPACITY / 2) {
      int before = cardinalityInBitmapRange(bitmap, 0, start);
      int after = cardinalityInBitmapRange(bitmap, end, MAX_CAPACITY);
      return cardinality - before - after;
    }
    return cardinalityInBitmapRange(bitmap, start, end);
  }
  public static int cardinalityInBitmapRange(long[] bitmap, int start, int end) {
    if (start >= end) {
      return 0;
    }
    int firstword = start / 64;
    int endword = (end - 1) / 64;
    if (firstword == endword) {
      return Long.bitCount(bitmap[firstword] & ((~0L << start) & (~0L >>> -end)));
    }
    int answer = Long.bitCount(bitmap[firstword] & (~0L << start));
    for (int i = firstword + 1; i < endword; i++) {
      answer += Long.bitCount(bitmap[i]);
    }
    answer += Long.bitCount(bitmap[endword] & (~0L >>> -end));
    return answer;
  }
}
