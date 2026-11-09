public class C08 { static final int MAX_CAPACITY=65536; long[] bitmap; int cardinality; C08(long[] b,int c){bitmap=b;cardinality=c;}
int cardinalityInRange(int start, int end) {
    // fast path: if range is large, compute complement using fewer operations
    if (cardinality != -1 && end - start > MAX_CAPACITY / 2) {
        int outside = 0;
        int startWord = start >>> 6;
        int endWord = (end - 1) >>> 6;
        int startBit = start & 0x3F;
        int endBit = end & 0x3F;
        // bits before start
        for (int i = 0; i < startWord; i++) {
            outside += Long.bitCount(bitmap[i]);
        }
        if (startBit != 0) {
            long mask = (1L << startBit) - 1L;
            outside += Long.bitCount(bitmap[startWord] & mask);
        }
        // bits after end
        for (int i = endWord + 1; i < bitmap.length; i++) {
            outside += Long.bitCount(bitmap[i]);
        }
        if (endBit != 0 && endWord < bitmap.length) {
            long mask = ~((1L << endBit) - 1L);
            outside += Long.bitCount(bitmap[endWord] & mask);
        }
        return cardinality - outside;
    }
    // direct computation of the range
    int count = 0;
    int startWord = start >>> 6;
    int endWord = (end - 1) >>> 6;
    int startBit = start & 0x3F;
    int endBit = end & 0x3F;
    if (startWord == endWord) {
        long mask;
        if (endBit == 0) {
            mask = -1L << startBit;
        } else {
            mask = ((1L << endBit) - 1L) ^ ((1L << startBit) - 1L);
        }
        return Long.bitCount(bitmap[startWord] & mask);
    }
    // first word
    if (startBit != 0) {
        long mask = -1L << startBit;
        count += Long.bitCount(bitmap[startWord] & mask);
    } else {
        count += Long.bitCount(bitmap[startWord]);
    }
    // middle full words
    for (int i = startWord + 1; i < endWord; i++) {
        count += Long.bitCount(bitmap[i]);
    }
    // last word
    if (endBit != 0) {
        long mask = (1L << endBit) - 1L;
        count += Long.bitCount(bitmap[endWord] & mask);
    } else {
        count += Long.bitCount(bitmap[endWord]);
    }
    return count;
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
